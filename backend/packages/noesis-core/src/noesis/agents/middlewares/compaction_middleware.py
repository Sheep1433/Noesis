"""Claude Code style conversation compaction for LangChain agents."""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass
from typing import Annotated, Any, Awaitable, Callable, NotRequired

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
    get_buffer_string,
)
from langgraph.types import Command

from noesis.runtime.logging import logger

try:
    from langgraph.errors import GraphBubbleUp
except ImportError:  # pragma: no cover
    class GraphBubbleUp(Exception):  # type: ignore[no-redef]
        pass

_SUMMARY_FAILURE_PREFIXES = (
    "<error>",
    "error:",
    "i cannot",
    "i can't",
    "i'm unable",
    "summary is unavailable",
)


class CompactionState(AgentState[ResponseT]):
    """Checkpointed policy state which is hidden from agent output."""

    compaction: NotRequired[Annotated[dict[str, Any], PrivateStateAttr]]


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
    archive_path: str | None
    preserved_messages: tuple[AnyMessage, ...]
    original_message_count: int
    mode: str
    attempts: int


def _summary_is_invalid(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return not normalized or any(normalized.startswith(prefix) for prefix in _SUMMARY_FAILURE_PREFIXES)


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
        backend: BackendProtocol | None = None,
        async_summarize: Callable[[list[AnyMessage]], Awaitable[str]] | None = None,
        request_token_counter: Callable[[ModelRequest[Any]], int] | None = None,
        keep_messages: int = 28,
        max_ptl_retries: int = 3,
        max_consecutive_failures: int = 3,
        archive_required: bool = True,
    ) -> None:
        super().__init__()
        self._token_counter = token_counter
        self._request_token_counter = request_token_counter
        self._summarize = summarize
        self._async_summarize = async_summarize
        self._thresholds = thresholds
        self._backend = backend
        self._keep_messages = max(1, keep_messages)
        self._max_ptl_retries = max(0, max_ptl_retries)
        self._max_failures = max(1, max_consecutive_failures)
        self._archive_required = archive_required

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

        ``/compact`` 命令在 runtime.context 设 ``manual_compact_requested=True``，
        CompactionMiddleware 检测到后强制触发压缩（绕过阈值和 breaker）。
        design §12: 手动 compact 不受自动熔断限制。
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

    def _archive(self, messages: list[AnyMessage], thread_id: str) -> str | None:
        if self._backend is None:
            return None
        digest = hashlib.sha256(get_buffer_string(messages).encode()).hexdigest()[:16]
        path = f"/conversation_history/{thread_id}/{digest}.md"
        result = self._backend.write(path, get_buffer_string(messages))
        if result is None or getattr(result, "error", None):
            raise RuntimeError("conversation archive write failed")
        return path

    # ---------- compaction events ----------

    def _emit_compaction_event(self, payload: dict[str, Any]) -> None:
        """同步发 noesis_compaction custom event（照 noesis_model_retry 模式）。"""
        try:
            from langgraph.config import get_stream_writer

            writer = get_stream_writer()
            try:
                writer(payload)
            except Exception:
                pass
            from langchain_core.callbacks import dispatch_custom_event

            dispatch_custom_event("noesis_compaction", payload)
        except GraphBubbleUp:
            raise
        except Exception:
            logger.debug("Failed to emit noesis_compaction event", exc_info=True)

    async def _aemit_compaction_event(self, payload: dict[str, Any]) -> None:
        """异步发 noesis_compaction custom event。"""
        try:
            from langgraph.config import get_stream_writer

            writer = get_stream_writer()
            try:
                writer(payload)
            except Exception:
                pass
            from langchain_core.callbacks import adispatch_custom_event

            await adispatch_custom_event("noesis_compaction", payload)
        except GraphBubbleUp:
            raise
        except Exception:
            logger.debug("Failed to emit async noesis_compaction event", exc_info=True)

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
        try:
            archive_path = self._archive(prefix, thread_id)
        except Exception:
            logger.exception("conversation archive failed thread_id={}", thread_id)
            if self._archive_required and self._backend is not None:
                return None
            archive_path = None
        return CompactionResult(summary, archive_path, tuple(preserved), len(messages), mode, attempts)

    async def _abuild(
        self, messages: list[AnyMessage], thread_id: str, mode: str
    ) -> CompactionResult | None:
        cutoff = _safe_cutoff(messages, self._keep_messages)
        if cutoff <= 0:
            return None
        prefix, preserved = messages[:cutoff], messages[cutoff:]
        summary_result = await self._asummarize_with_retry(prefix)
        if summary_result is None:
            return None
        summary, attempts = summary_result
        try:
            archive_path = self._archive(prefix, thread_id)
        except Exception:
            logger.exception("conversation archive failed thread_id={}", thread_id)
            if self._archive_required and self._backend is not None:
                return None
            archive_path = None
        return CompactionResult(summary, archive_path, tuple(preserved), len(messages), mode, attempts)

    @staticmethod
    def _summary_message(result: CompactionResult) -> HumanMessage:
        boundary = hashlib.sha256(
            f"{result.summary_text}:{result.original_message_count}".encode()
        ).hexdigest()[:16]
        return HumanMessage(
            content=f"Here is a summary of the conversation to date:\n\n{result.summary_text}",
            additional_kwargs={
                "lc_source": "summarization",
                "compact_boundary": boundary,
                "archive_path": result.archive_path,
                "compaction_mode": result.mode,
            },
        )

    def _request_with_result(
        self, request: ModelRequest[ContextT], result: CompactionResult
    ) -> ModelRequest[ContextT]:
        policy = self._policy_state(request.state)
        cutoff = self._raw_cutoff(policy, result)
        policy.update(
            {
                "consecutive_failures": 0,
                "last_mode": result.mode,
                "last_archive_path": result.archive_path,
                "summary_attempts": result.attempts,
                "event": {
                    "summary_message": self._summary_message(result),
                    "cutoff_index": cutoff,
                    "archive_path": result.archive_path,
                },
            }
        )
        return request.override(
            messages=[self._summary_message(result), *result.preserved_messages],
            state={**request.state, "compaction": policy},
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
        cutoff = self._raw_cutoff(previous_policy or {}, result)
        return Command(
            update={
                "compaction": {
                    "consecutive_failures": 0,
                    "last_mode": result.mode,
                    "last_archive_path": result.archive_path,
                    "summary_attempts": result.attempts,
                    "event": {
                        "summary_message": self._summary_message(result),
                        "cutoff_index": cutoff,
                        "archive_path": result.archive_path,
                    },
                },
            }
        )

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
        if self._request_tokens(effective_request) >= self._thresholds.hard_stop_at:
            raise ContextOverflowError("effective request exceeds the compaction hard guard")
        try:
            response = handler(effective_request)
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
            response = handler(effective_request)
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
            else:
                effective_request = self._failure_request(effective_request)
                await self._aemit_compaction_event(self._build_failed_payload(mode, "summary_invalid"))
                failed_request = effective_request
        if self._request_tokens(effective_request) >= self._thresholds.hard_stop_at:
            raise ContextOverflowError("effective request exceeds the compaction hard guard")
        try:
            response = await handler(effective_request)
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
            response = await handler(effective_request)
            compacted = reactive
        if compacted:
            return self._with_command(response, self._state_command(compacted, self._policy_state(request.state)))
        if failed_request:
            return self._with_command(response, self._failure_command(failed_request))
        return response

    def compact(self, state: CompactionState[Any], *, thread_id: str, instructions: str | None = None) -> dict[str, Any]:
        """Host/manual entry point using the same compaction engine."""
        messages = list(state.get("messages", []))
        result = self._build(
            messages,
            thread_id,
            "manual",
            keep_messages=min(self._keep_messages, max(1, len(messages) // 3)),
            instructions=instructions,
        )
        if result is None:
            raise RuntimeError("manual compaction failed")
        return self._state_command(result, self._policy_state(state)).update or {}


__all__ = ["CompactionMiddleware", "CompactionResult", "CompactionState", "CompactionThresholds"]
