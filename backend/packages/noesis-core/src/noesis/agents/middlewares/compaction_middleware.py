"""Claude Code style conversation compaction for LangChain agents."""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass
from typing import Annotated, Any, Awaitable, Callable, Mapping, NotRequired

from deepagents.backends import BackendProtocol
from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ExtendedModelResponse,
    ModelCallResult,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
    ResponseT,
)
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    ToolMessage,
)
from langgraph.types import Command

from noesis.agents.middlewares._events import aemit_noesis_event, emit_noesis_event
from noesis.runtime.logging import logger

_SUMMARY_FAILURE_PREFIXES = (
    "<error>",
    "error:",
    "i cannot",
    "i can't",
    "i'm unable",
    "summary is unavailable",
)

# 摘要生成调用的 run tag：该调用在图内执行，其 astream_events 事件若不过滤
# 会被 bridge 当成正文模型调用——摘要 JSON 变成 assistant text part 上屏，
# usage/步数也混入基础设施调用。stream_agent_events 据此丢弃。
COMPACTION_SUMMARY_TAG = "noesis_compaction_summary"


class CompactionState(AgentState[ResponseT]):
    """Checkpointed policy state which is hidden from agent output."""

    compaction: NotRequired[Annotated[dict[str, Any], PrivateStateAttr]]


# State keys this middleware owns; subagent isolation must carry these over.
PRIVATE_STATE_KEYS: tuple[str, ...] = ("compaction",)


@dataclass(frozen=True)
class CompactionThresholds:
    model_input_limit: int
    summary_output_reserve: int
    transient_request_buffer: int
    final_request_guard: int = 0

    def __post_init__(self) -> None:
        values = (
            self.model_input_limit,
            self.summary_output_reserve,
            self.transient_request_buffer,
            self.final_request_guard,
        )
        if any(value < 0 for value in values) or self.model_input_limit == 0:
            raise ValueError("compaction thresholds must be non-negative and input limit positive")
        if self.auto_compact_at <= 0 or self.hard_stop_at <= 0:
            raise ValueError("compaction reserves exceed the model input limit")

    @property
    def effective_limit(self) -> int:
        return self.model_input_limit - self.summary_output_reserve

    @property
    def auto_compact_at(self) -> int:
        return self.effective_limit - self.transient_request_buffer

    @property
    def hard_stop_at(self) -> int:
        return self.effective_limit - self.final_request_guard


@dataclass(frozen=True)
class CompactionResult:
    summary_text: str
    preserved_messages: tuple[AnyMessage, ...]
    original_message_count: int
    mode: str
    attempts: int


@dataclass(frozen=True)
class ManualCompactionState:
    """Host-level compaction outcome after the checkpoint callback succeeds."""

    result: CompactionResult
    pre_message_count: int
    post_message_count: int
    pre_tokens: int
    post_tokens: int


CheckpointWriter = Callable[[dict[str, Any]], Awaitable[None]]
# 压缩成功后写会话遮蔽边界（session-history-search）：参数为 thread_id。
# 仅 async 路径调用（awrap_model_call / acompact_state）；同步 wrap_model_call
# 为离线评测路径，不接 DB。
BoundaryWriter = Callable[[str], Awaitable[None]]


def _summary_is_invalid(text: str) -> bool:
    normalized = (text or "").strip().lower()
    if not normalized or any(normalized.startswith(prefix) for prefix in _SUMMARY_FAILURE_PREFIXES):
        return True
    # 过短判定：进入压缩的会话至少数万 token，其检查点摘要不可能只有
    # 几百字符——实测 766K 输入偶发产出 423 字符的原文片段复述（模型
    # 回显指令模板头 + 倾倒尾部片段），骗过前缀/复读检测后静默替换历史
    if len(normalized) < _MIN_SUMMARY_CHARS:
        return True
    return _summary_is_degenerate_repetition(normalized)


# 摘要最小体量：8 节 checkpoint 的骨架（标题 + 每节至少一行）低于此值
# 即为退化输出；正常摘要实测 8K+ 字符，阈值取数量级下界
_MIN_SUMMARY_CHARS = 1_000


def _summary_is_degenerate_repetition(text: str) -> bool:
    """复读检测：超长上下文摘要可能退化为复读循环（745K 实证同一行重复
    数千次仍被判"有效"入库）。宁可压缩失败走重试/熔断，不可让复读
    摘要静默替换真实历史。"""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 20:
        return False
    longest = current = 1
    for i in range(1, len(lines)):
        current = current + 1 if lines[i] == lines[i - 1] else 1
        longest = max(longest, current)
    if longest >= 10:
        return True
    # 唯一行占比过低 = 倾倒式复读（八节 checkpoint 的行彼此不同，不会误杀）
    return len(lines) >= 50 and len(set(lines)) / len(lines) < 0.2


def _safe_cutoff(messages: list[AnyMessage], keep_messages: int) -> int:
    """Return a boundary that never splits an AI tool-call round."""
    if len(messages) <= 1:
        return 0
    cutoff = max(1, len(messages) - keep_messages)
    if cutoff >= len(messages) or not isinstance(messages[cutoff], ToolMessage):
        return cutoff

    result_ids: set[str] = set()
    index = cutoff
    while index < len(messages) and isinstance(messages[index], ToolMessage):
        if messages[index].tool_call_id:
            result_ids.add(messages[index].tool_call_id)
        index += 1
    for index in range(cutoff - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, AIMessage):
            continue
        call_ids = {call.get("id") for call in message.tool_calls if call.get("id")}
        if call_ids & result_ids:
            return index
    return cutoff


def _drop_oldest_api_round(messages: list[AnyMessage]) -> list[AnyMessage]:
    """Drop one complete oldest conversational round and guarantee progress."""
    if len(messages) <= 1:
        return []
    index = 1
    if isinstance(messages[0], AIMessage) and messages[0].tool_calls:
        call_ids = {call.get("id") for call in messages[0].tool_calls if call.get("id")}
        while index < len(messages):
            message = messages[index]
            if not isinstance(message, ToolMessage) or message.tool_call_id not in call_ids:
                break
            index += 1
    else:
        while index < len(messages) and not isinstance(messages[index], HumanMessage):
            index += 1
    return messages[index:]


def _message_text(content: Any) -> str:
    """提取消息的纯文本；多模态内容只取文字部分（图片不参与原话保留）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _retained_user_messages(
    messages: list[AnyMessage], budget_tokens: int
) -> list[HumanMessage]:
    """被压缩区用户消息原文保留（对齐 codex compact 的 collect_user_messages）。

    从最新往最旧装满预算为止，装不下的最旧一条按剩余预算截断；只收
    HumanMessage 的文字内容。摘要只负责决策与状态，用户说过的话靠这里
    结构性兜底，不赌摘要模型的行为。
    """
    retained: list[HumanMessage] = []
    remaining = budget_tokens
    for message in reversed(messages):
        if remaining <= 0:
            break
        if not isinstance(message, HumanMessage):
            continue
        text = _message_text(message.content).strip()
        if not text:
            continue
        tokens = max(1, len(text) // 4)
        if tokens > remaining:
            retained.append(
                HumanMessage(content=text[: remaining * 4].rstrip() + "\n…[truncated]")
            )
            break
        retained.append(HumanMessage(content=text))
        remaining -= tokens
    retained.reverse()
    return retained


# 上下文契约：压缩后的投影头部对 Agent 声明上下文状态——构成、盲区、
# 恢复通道与使用原则。按内容类别声明（通用机制），不携带任何会话特定
# 信息；恢复通道措辞与工具挂载无关（闭卷场景下模型被引导明确说明缺失
# 而不是翻找或猜测）。症状驱动：闭卷组在答案就在上下文里时仍空转工具、
# 检索组在可答对时被工具循环带偏，根源都是模型不知道自己有什么、缺什么。
_CONTEXT_CONTRACT = "\n".join([
    "[上下文状态] 本会话历史已压缩。当前上下文的构成：",
    "1. 本消息：被压缩区的结构化摘要",
    "2. 被压缩区内用户消息的原文（按预算装回，最旧的超出预算部分截断）",
    "3. 最近若干轮的完整原文（含工具结果）",
    "因此以下类别的内容可能不在你的视野内：早期的工具输出原文、AI 回复"
    "的原文与解释细节、精确数值与命令原文。",
    "",
    "[使用原则] 先基于当前上下文作答——结论、决策与用户原话都在其中；"
    "确信所需内容属于上述被遮蔽类别时，用已挂载的会话历史检索工具定向"
    "找回；两者都没有就明确说明该内容已随压缩不可得。不要翻找文件系统，"
    "不要猜测。",
    "",
    "被压缩区的摘要如下：",
])


class CompactionMiddleware(
    AgentMiddleware[CompactionState[ResponseT], ContextT, ResponseT]
):
    """Compact effective history with persisted breaker and reactive recovery."""

    state_schema = CompactionState

    def __init__(
        self,
        *,
        token_counter: Callable[[list[AnyMessage]], int],
        summarize: Callable[[list[AnyMessage]], str],
        thresholds: CompactionThresholds,
        async_summarize: Callable[[list[AnyMessage]], Awaitable[str]] | None = None,
        request_token_counter: Callable[[ModelRequest[Any]], int] | None = None,
        keep_messages: int = 28,
        max_ptl_retries: int = 3,
        max_consecutive_failures: int = 3,
        boundary_writer: BoundaryWriter | None = None,
        user_message_budget_tokens: int = 20_000,
    ) -> None:
        super().__init__()
        self._token_counter = token_counter
        self._request_token_counter = request_token_counter
        self._summarize = summarize
        self._async_summarize = async_summarize
        self._thresholds = thresholds
        self._keep_messages = max(1, keep_messages)
        self._max_ptl_retries = max(0, max_ptl_retries)
        self._max_failures = max(1, max_consecutive_failures)
        self._boundary_writer = boundary_writer
        # 0 = 关闭用户原话装回（仅测试/对照用）
        self._user_message_budget_tokens = max(0, user_message_budget_tokens)

    @staticmethod
    def _policy_state(state: dict[str, Any]) -> dict[str, Any]:
        return dict(state.get("compaction") or {})

    def _request_tokens(self, request: ModelRequest[Any]) -> int:
        if self._request_token_counter is not None:
            return self._request_token_counter(request)
        messages = list(request.messages)
        if request.system_message is not None:
            messages.insert(0, request.system_message)
        message_tokens = self._token_counter(messages)
        tool_tokens = sum(max(1, len(repr(tool)) // 4) for tool in request.tools)
        return message_tokens + tool_tokens

    def _project(self, request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
        """Rebuild effective history from the checkpointed compaction event."""
        policy = self._policy_state(request.state)
        event = policy.get("event")
        if not isinstance(event, dict):
            return request
        summary = event.get("summary_message")
        cutoff = event.get("cutoff_index")
        if not isinstance(summary, HumanMessage) or not isinstance(cutoff, int):
            return request
        raw_messages = list(request.messages)
        if cutoff < 0 or cutoff > len(raw_messages):
            return request
        return request.override(messages=[summary, *raw_messages[cutoff:]])

    def _final_request(
        self, effective_request: ModelRequest[ContextT], raw_request: ModelRequest[Any]
    ) -> ModelRequest[ContextT]:
        """最终请求组装：在投影结果头部装回被压缩区用户消息原文。

        注入只发生在发给模型的请求边界——压缩机制（摘要输入、cutoff
        算术、事件结构）看到的仍是 [summary, *raw[cutoff:]]，语义不变。
        raw 永远完整保留在 checkpoint，因此装回是从原文幂等提取，重复
        压缩不丢内容。
        """
        if self._user_message_budget_tokens <= 0:
            return effective_request
        event = self._policy_state(effective_request.state).get("event")
        if not isinstance(event, dict) or not isinstance(event.get("cutoff_index"), int):
            return effective_request
        cutoff = event["cutoff_index"]
        raw_messages = list(raw_request.messages)
        if cutoff <= 0 or cutoff > len(raw_messages):
            return effective_request
        users = _retained_user_messages(
            raw_messages[:cutoff], self._user_message_budget_tokens
        )
        if not users:
            return effective_request
        return effective_request.override(
            messages=[*users, *effective_request.messages]
        )

    def _should_auto_compact(self, request: ModelRequest[Any]) -> bool:
        policy = self._policy_state(request.state)
        if policy.get("in_progress"):
            return False
        # manual compact 请求绕过 breaker 和阈值检查
        if self._manual_compact_requested(request):
            return True
        if int(policy.get("consecutive_failures", 0)) >= self._max_failures:
            return False
        return self._request_tokens(request) >= self._thresholds.auto_compact_at

    @staticmethod
    def _manual_compact_requested(request: ModelRequest[Any]) -> bool:
        """检查 runtime.context 是否有 manual compact 请求标记。

        Host/runtime entry may set ``manual_compact_requested=True`` to force
        the normal model-call seam to compact, bypassing threshold and breaker.
        The direct ``/compact`` command uses ``acompact_state`` instead, so it
        does not create a model turn.
        """
        runtime = request.runtime
        context = getattr(runtime, "context", None)
        if isinstance(context, dict):
            return bool(context.get("manual_compact_requested"))
        return False

    @staticmethod
    def _thread_id(request: ModelRequest[Any]) -> str:
        runtime = request.runtime
        context = getattr(runtime, "context", None)
        if isinstance(context, dict) and context.get("thread_id"):
            return str(context["thread_id"])
        config = getattr(runtime, "config", None)
        if isinstance(config, dict):
            thread_id = (config.get("configurable") or {}).get("thread_id")
            if thread_id:
                return str(thread_id)
        return "default"

    # ---------- compaction events ----------

    def _emit_compaction_event(self, payload: dict[str, Any]) -> None:
        """同步发 noesis_compaction custom event。"""
        emit_noesis_event("noesis_compaction", payload)

    async def _aemit_compaction_event(self, payload: dict[str, Any]) -> None:
        """异步发 noesis_compaction custom event。"""
        await aemit_noesis_event("noesis_compaction", payload)

    async def _awrite_boundary(self, thread_id: str) -> None:
        """压缩成功后写遮蔽边界；失败只记日志（边界缺失=检索降级，不阻断压缩）。"""
        if self._boundary_writer is None:
            return
        try:
            await self._boundary_writer(thread_id)
        except Exception:
            logger.warning("compaction boundary write failed thread_id={}", thread_id)

    def _build_started_payload(self, mode: str, pre_tokens: int) -> dict[str, Any]:
        return {
            "compaction_type": "started",
            "mode": mode,
            "message": "正在压缩对话上下文…",
            "pre_tokens": pre_tokens,
        }

    def _build_completed_payload(
        self, mode: str, pre_tokens: int, post_tokens: int, messages_summarized: int,
    ) -> dict[str, Any]:
        return {
            "compaction_type": "completed",
            "mode": mode,
            "message": f"已压缩 {messages_summarized} 条对话历史",
            "pre_tokens": pre_tokens,
            "post_tokens": post_tokens,
            "messages_summarized": messages_summarized,
        }

    def _build_failed_payload(self, mode: str, reason: str) -> dict[str, Any]:
        return {
            "compaction_type": "failed",
            "mode": mode,
            "reason": reason,
        }


    def _summarize_with_retry(self, messages: list[AnyMessage]) -> tuple[str, int] | None:
        batch = messages
        for attempt in range(1, self._max_ptl_retries + 2):
            if not batch:
                return None
            try:
                summary = self._summarize(batch)
            except ContextOverflowError:
                if attempt > self._max_ptl_retries:
                    return None
                reduced = _drop_oldest_api_round(batch)
                if len(reduced) >= len(batch):
                    return None
                batch = reduced
                continue
            except Exception:
                logger.exception("conversation summary failed attempt={}", attempt)
                return None
            if _summary_is_invalid(summary):
                return None
            return summary.strip(), attempt
        return None

    async def _asummarize_with_retry(
        self, messages: list[AnyMessage]
    ) -> tuple[str, int] | None:
        batch = messages
        for attempt in range(1, self._max_ptl_retries + 2):
            if not batch:
                return None
            try:
                if self._async_summarize is not None:
                    summary = await self._async_summarize(batch)
                else:
                    candidate = self._summarize(batch)
                    summary = await candidate if inspect.isawaitable(candidate) else candidate
            except ContextOverflowError:
                if attempt > self._max_ptl_retries:
                    return None
                reduced = _drop_oldest_api_round(batch)
                if len(reduced) >= len(batch):
                    return None
                batch = reduced
                continue
            except Exception:
                logger.exception("async conversation summary failed attempt={}", attempt)
                return None
            if _summary_is_invalid(summary):
                return None
            return summary.strip(), attempt
        return None

    def _build(
        self,
        messages: list[AnyMessage],
        thread_id: str,
        mode: str,
        *,
        keep_messages: int | None = None,
        instructions: str | None = None,
    ) -> CompactionResult | None:
        cutoff = _safe_cutoff(messages, keep_messages or self._keep_messages)
        if cutoff <= 0:
            return None
        prefix, preserved = messages[:cutoff], messages[cutoff:]
        summary_input = list(prefix)
        if instructions:
            summary_input.append(
                HumanMessage(content=f"Retain these details in the summary: {instructions}")
            )
        summary_result = self._summarize_with_retry(summary_input)
        if summary_result is None:
            return None
        summary, attempts = summary_result
        return CompactionResult(summary, tuple(preserved), len(messages), mode, attempts)

    async def _abuild(
        self,
        messages: list[AnyMessage],
        thread_id: str,
        mode: str,
        *,
        instructions: str | None = None,
    ) -> CompactionResult | None:
        cutoff = _safe_cutoff(messages, self._keep_messages)
        if cutoff <= 0:
            return None
        prefix, preserved = messages[:cutoff], messages[cutoff:]
        summary_input = list(prefix)
        if instructions:
            summary_input.append(
                HumanMessage(content=f"Retain these details in the summary: {instructions}")
            )
        summary_result = await self._asummarize_with_retry(summary_input)
        if summary_result is None:
            return None
        summary, attempts = summary_result
        return CompactionResult(summary, tuple(preserved), len(messages), mode, attempts)

    @staticmethod
    def _summary_message(result: CompactionResult) -> HumanMessage:
        boundary = hashlib.sha256(
            f"{result.summary_text}:{result.original_message_count}".encode()
        ).hexdigest()[:16]
        return HumanMessage(
            content=f"{_CONTEXT_CONTRACT}\n\n{result.summary_text}",
            additional_kwargs={
                "lc_source": "summarization",
                "compact_boundary": boundary,
                "compaction_mode": result.mode,
            },
        )

    def _request_with_result(
        self, request: ModelRequest[ContextT], result: CompactionResult
    ) -> ModelRequest[ContextT]:
        policy = self._policy_state(request.state)
        state_update = self.state_update_for_result(result, policy)
        return request.override(
            messages=[self._summary_message(result), *result.preserved_messages],
            state={**request.state, **state_update},
        )

    def state_update_for_result(
        self, result: CompactionResult, previous_policy: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Build the checkpoint update shared by model-call and host compaction."""
        policy = dict(previous_policy or {})
        cutoff = self._raw_cutoff(policy, result)
        policy.update(
            {
                "consecutive_failures": 0,
                "last_mode": result.mode,
                "summary_attempts": result.attempts,
                "event": {
                    "summary_message": self._summary_message(result),
                    "cutoff_index": cutoff,
                },
            }
        )
        return {"compaction": policy}

    async def acompact_state(
        self,
        state: Mapping[str, Any],
        thread_id: str,
        *,
        instructions: str | None = None,
        checkpoint: CheckpointWriter | None = None,
    ) -> ManualCompactionState | None:
        """Compact checkpointed history without starting a model turn.

        This is the host/runtime seam used by ``/compact``. The effective history
        is projected exactly as it is before a normal model call; only the
        checkpointed compaction policy changes, so raw messages remain available
        for resume and future re-compaction.
        """
        raw_messages = list(state.get("messages") or [])
        if not raw_messages:
            return None

        request = ModelRequest(
            model=object(),  # type: ignore[arg-type]
            messages=raw_messages,
            state=dict(state),
        )
        projected_request = self._project(request)
        effective_messages = list(projected_request.messages)
        # 指标按最终视图（含被压缩区用户原话装回）报告，与压缩后下一次
        # 模型调用实际所见一致
        pre_view = list(self._final_request(projected_request, request).messages)
        result = await self._abuild(
            effective_messages,
            thread_id,
            "manual",
            instructions=instructions,
        )
        if result is None:
            return None

        state_update = self.state_update_for_result(
            result, self._policy_state(dict(state))
        )
        if checkpoint is not None:
            await checkpoint(state_update)
        await self._awrite_boundary(thread_id)

        compacted_request = ModelRequest(
            model=object(),  # type: ignore[arg-type]
            messages=[self._summary_message(result), *result.preserved_messages],
            state={**dict(state), **state_update},
        )
        post_view = list(self._final_request(compacted_request, request).messages)
        return ManualCompactionState(
            result=result,
            pre_message_count=len(pre_view),
            post_message_count=len(post_view),
            pre_tokens=self._token_counter(pre_view),
            post_tokens=self._token_counter(post_view),
        )

    @staticmethod
    def _raw_cutoff(policy: dict[str, Any], result: CompactionResult) -> int:
        effective_cutoff = result.original_message_count - len(result.preserved_messages)
        previous = policy.get("event")
        if isinstance(previous, dict) and isinstance(previous.get("cutoff_index"), int):
            return int(previous["cutoff_index"]) + max(0, effective_cutoff - 1)
        return effective_cutoff

    def _state_command(
        self, result: CompactionResult, previous_policy: dict[str, Any] | None = None
    ) -> Command[Any]:
        return Command(update=self.state_update_for_result(result, previous_policy))

    def _failure_request(self, request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
        policy = self._policy_state(request.state)
        policy["consecutive_failures"] = int(policy.get("consecutive_failures", 0)) + 1
        return request.override(state={**request.state, "compaction": policy})

    @staticmethod
    def _failure_command(request: ModelRequest[Any]) -> Command[Any]:
        return Command(update={"compaction": dict(request.state.get("compaction") or {})})

    @staticmethod
    def _with_command(result: ModelCallResult, command: Command[Any]) -> ExtendedModelResponse[Any]:
        if isinstance(result, ExtendedModelResponse):
            response = result.model_response
            existing = result.command
        elif isinstance(result, AIMessage):
            response = ModelResponse(result=[result])
            existing = None
        else:
            response = result
            existing = None
        if existing is not None and isinstance(existing.update, dict) and isinstance(command.update, dict):
            command = Command(
                graph=existing.graph,
                update={**existing.update, **command.update},
                resume=existing.resume,
                goto=existing.goto,
            )
        return ExtendedModelResponse(model_response=response, command=command)

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelCallResult],
    ) -> ModelCallResult:
        effective_request = self._project(request)
        compacted: CompactionResult | None = None
        failed_request: ModelRequest[ContextT] | None = None
        if self._should_auto_compact(effective_request):
            mode = "manual" if self._manual_compact_requested(effective_request) else "auto"
            pre_tokens = self._request_tokens(effective_request)
            self._emit_compaction_event(self._build_started_payload(mode, pre_tokens))
            compacted = self._build(list(effective_request.messages), self._thread_id(request), mode)
            if compacted is not None:
                effective_request = self._request_with_result(effective_request, compacted)
                self._emit_compaction_event(self._build_completed_payload(
                    mode, pre_tokens, self._request_tokens(effective_request),
                    compacted.original_message_count - len(compacted.preserved_messages),
                ))
            else:
                effective_request = self._failure_request(effective_request)
                self._emit_compaction_event(self._build_failed_payload(mode, "summary_invalid"))
                failed_request = effective_request
        final_request = self._final_request(effective_request, request)
        if self._request_tokens(final_request) >= self._thresholds.hard_stop_at:
            raise ContextOverflowError("effective request exceeds the compaction hard guard")
        try:
            response = handler(final_request)
        except ContextOverflowError:
            pre_tokens = self._request_tokens(effective_request)
            self._emit_compaction_event(self._build_started_payload("reactive", pre_tokens))
            reactive = self._build(list(effective_request.messages), self._thread_id(request), "reactive")
            if reactive is None:
                self._emit_compaction_event(self._build_failed_payload("reactive", "summary_invalid"))
                raise
            effective_request = self._request_with_result(effective_request, reactive)
            self._emit_compaction_event(self._build_completed_payload(
                "reactive", pre_tokens, self._request_tokens(effective_request),
                reactive.original_message_count - len(reactive.preserved_messages),
            ))
            final_request = self._final_request(effective_request, request)
            response = handler(final_request)
            compacted = reactive
        if compacted:
            return self._with_command(response, self._state_command(compacted, self._policy_state(request.state)))
        if failed_request:
            return self._with_command(response, self._failure_command(failed_request))
        return response

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelCallResult]],
    ) -> ModelCallResult:
        effective_request = self._project(request)
        compacted: CompactionResult | None = None
        failed_request: ModelRequest[ContextT] | None = None
        if self._should_auto_compact(effective_request):
            mode = "manual" if self._manual_compact_requested(effective_request) else "auto"
            pre_tokens = self._request_tokens(effective_request)
            await self._aemit_compaction_event(self._build_started_payload(mode, pre_tokens))
            compacted = await self._abuild(list(effective_request.messages), self._thread_id(request), mode)
            if compacted is not None:
                effective_request = self._request_with_result(effective_request, compacted)
                await self._aemit_compaction_event(self._build_completed_payload(
                    mode, pre_tokens, self._request_tokens(effective_request),
                    compacted.original_message_count - len(compacted.preserved_messages),
                ))
                await self._awrite_boundary(self._thread_id(request))
            else:
                effective_request = self._failure_request(effective_request)
                await self._aemit_compaction_event(self._build_failed_payload(mode, "summary_invalid"))
                failed_request = effective_request
        final_request = self._final_request(effective_request, request)
        if self._request_tokens(final_request) >= self._thresholds.hard_stop_at:
            raise ContextOverflowError("effective request exceeds the compaction hard guard")
        try:
            response = await handler(final_request)
        except ContextOverflowError:
            pre_tokens = self._request_tokens(effective_request)
            await self._aemit_compaction_event(self._build_started_payload("reactive", pre_tokens))
            reactive = await self._abuild(list(effective_request.messages), self._thread_id(request), "reactive")
            if reactive is None:
                await self._aemit_compaction_event(self._build_failed_payload("reactive", "summary_invalid"))
                raise
            effective_request = self._request_with_result(effective_request, reactive)
            await self._aemit_compaction_event(self._build_completed_payload(
                "reactive", pre_tokens, self._request_tokens(effective_request),
                reactive.original_message_count - len(reactive.preserved_messages),
            ))
            await self._awrite_boundary(self._thread_id(request))
            final_request = self._final_request(effective_request, request)
            response = await handler(final_request)
            compacted = reactive
        if compacted:
            return self._with_command(response, self._state_command(compacted, self._policy_state(request.state)))
        if failed_request:
            return self._with_command(response, self._failure_command(failed_request))
        return response


__all__ = [
    "BoundaryWriter",
    "CompactionMiddleware",
    "CompactionResult",
    "CompactionState",
    "CompactionThresholds",
    "ManualCompactionState",
    "PRIVATE_STATE_KEYS",
]
