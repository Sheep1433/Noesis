"""Session-level LLM stats middleware: tracks steps/timing/tokens.

在 wrap_model_call 层采集模型调用指标，发 noesis_stats_update custom event，
bridge 转成 stats-update SSE 下发前端。

采集：步数、LLM 耗时、输入/输出 token、缓存命中。

状态外置到 SessionStatsRegistry（按 session_id 键控）：主 Agent 与 subagent
各自的中间件实例写同一份会话级累计，stats-update 从 registry 快照组装，
避免多实例各发各的在前端相互覆盖、消耗总量不完整。
session_id 缺失时退回实例内累计（兼容无会话上下文的构造路径）。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Awaitable
from typing_extensions import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    ModelCallResult,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage
from langgraph.errors import GraphBubbleUp

from noesis.agents.middlewares.session_stats_registry import SessionStatsRegistry
from noesis.chat.event_mapping.usage_normalize import normalize_usage

logger = logging.getLogger(__name__)

_LOCAL_STATS_FIELDS = (
    "turns",
    "steps",
    "llm_ms",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)


class SessionStatsMiddleware(AgentMiddleware[AgentState]):
    """会话级统计：步数/LLM 耗时/token/缓存。主/子实例经 registry 共享累计。"""

    def __init__(self, *, session_id: str = "", count_turns: bool = True, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._session_id = session_id
        # subagent（profile=SUBAGENT）的首步也是 HumanMessage 结尾，但不是用户
        # 提问——不计轮数，步数/token 照常计入会话级累计。
        self._count_turns = count_turns
        # 无 session_id 时的回退存储（行为同旧版：实例内累计）
        self._local_stats: dict[str, float] = {field: 0.0 for field in _LOCAL_STATS_FIELDS}

    def _stats_snapshot(self) -> dict[str, float]:
        """当前累计快照：优先 registry，无 session_id 用实例内。"""
        if self._session_id:
            snapshot = SessionStatsRegistry.peek(self._session_id)
            return snapshot if snapshot is not None else dict(self._local_stats)
        return dict(self._local_stats)

    def _resolve_msg(self, response: Any) -> AIMessage | None:
        """从 ModelResponse / AIMessage 提取底层 AIMessage。

        ``wrap_model_call`` 的 handler 返回 ``ModelResponse``，消息列表在
        ``result`` 字段（非 ``messages``）；误读 ``messages`` 会导致
        streaming 路径下始终取不到 usage，token 全为 0。
        """
        # ModelResponse.result: list[BaseMessage]
        result = getattr(response, "result", None)
        if result:
            msg = result[0] if len(result) > 0 else None
        else:
            # 兼容 messages 字段或直接 AIMessage
            messages = getattr(response, "messages", None)
            if messages and len(messages) > 0:
                msg = messages[0]
            elif isinstance(response, AIMessage):
                msg = response
            else:
                return None
        return msg if isinstance(msg, AIMessage) else None

    def _extract_usage(self, response: Any) -> dict[str, Any] | None:
        """从 ModelResponse / AIMessage 提取并归一化 token 用量。

        覆盖两种来源：LangChain ``usage_metadata``（LangChain 已归一为
        input_tokens/output_tokens/input_token_details.cache_read）与 OpenAI
        兼容 provider 落在 ``response_metadata.token_usage`` 的原始字段
        （prompt_tokens/completion_tokens/prompt_tokens_details.cached_tokens）。
        统一经 ``normalize_usage`` 归一化，避免字段名不匹配导致 token 始终为 0。
        """
        msg = self._resolve_msg(response)
        if msg is None:
            return None
        # 优先 usage_metadata（LangChain 已归一）；缺失时回退 response_metadata.token_usage
        usage = getattr(msg, "usage_metadata", None)
        if not isinstance(usage, dict) or not usage:
            response_metadata = getattr(msg, "response_metadata", None) or {}
            usage = response_metadata.get("token_usage") if isinstance(response_metadata, dict) else None
        if not usage:
            return None
        return normalize_usage(usage)

    def _is_new_turn(self, request: ModelRequest) -> bool:
        """判断本次 model call 是否开启新一轮（user 发起新消息）。

        新轮次的首个 step 通常 messages 末尾是 HumanMessage（用户提问）；
        同轮后续 step（工具结果回来后再调模型）末尾是 ToolMessage。
        以此区分轮次与步数：用户问一次 = 1 轮，该轮内模型可被调多次 = 多步。
        """
        messages = getattr(request, "messages", None)
        if messages is None and isinstance(request, dict):
            messages = request.get("messages")
        messages = messages or []
        if not messages:
            return False
        from langchain_core.messages import HumanMessage
        return isinstance(messages[-1], HumanMessage)

    def _record_step(self, response: Any, llm_ms: float, request: ModelRequest | None = None) -> None:
        """记录一次模型调用的指标，累加进 registry（或实例回退存储）。"""
        delta: dict[str, float] = {"steps": 1, "llm_ms": llm_ms}

        # 轮数：仅主 Agent 在新轮次首个 step（messages 末尾为 HumanMessage）时 +1；
        # 同轮后续模型调用（工具结果回写后）与 subagent 调用不计新轮。
        if self._count_turns and request is not None and self._is_new_turn(request):
            delta["turns"] = 1

        usage = self._extract_usage(response)
        if usage:
            delta["input_tokens"] = int(usage.get("input_tokens") or 0)
            delta["output_tokens"] = int(usage.get("output_tokens") or 0)
            input_details = usage.get("input_token_details") or {}
            delta["cache_read_tokens"] = int(input_details.get("cache_read") or 0)
            delta["cache_write_tokens"] = int(input_details.get("cache_write") or 0)

        if self._session_id:
            SessionStatsRegistry.add(self._session_id, delta)
        else:
            for field, value in delta.items():
                self._local_stats[field] += value

    def _emit_stats(self) -> None:
        """发 noesis_stats_update custom event。"""
        try:
            from langgraph.config import get_stream_writer
            from langchain_core.callbacks import dispatch_custom_event

            payload = {"type": "noesis_stats_update", **self._stats_snapshot()}
            writer = get_stream_writer()
            try:
                writer(payload)
            except Exception:
                pass
            dispatch_custom_event("noesis_stats_update", payload)
        except GraphBubbleUp:
            raise
        except Exception:
            logger.debug("Failed to emit noesis_stats_update", exc_info=True)

    async def _aemit_stats(self) -> None:
        try:
            from langgraph.config import get_stream_writer
            from langchain_core.callbacks import adispatch_custom_event

            payload = {"type": "noesis_stats_update", **self._stats_snapshot()}
            writer = get_stream_writer()
            try:
                writer(payload)
            except Exception:
                pass
            await adispatch_custom_event("noesis_stats_update", payload)
        except GraphBubbleUp:
            raise
        except Exception:
            logger.debug("Failed to emit async noesis_stats_update", exc_info=True)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        start = time.perf_counter()
        response = handler(request)
        llm_ms = max(0, (time.perf_counter() - start) * 1000)
        self._record_step(response, llm_ms, request)
        self._emit_stats()
        return response

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        start = time.perf_counter()
        response = await handler(request)
        llm_ms = max(0, (time.perf_counter() - start) * 1000)
        self._record_step(response, llm_ms, request)
        await self._aemit_stats()
        return response


__all__ = ["SessionStatsMiddleware"]
