"""Session-level LLM stats middleware: tracks steps/timing/tokens.

在 wrap_model_call 层采集模型调用指标，发 noesis_stats_update custom event，
bridge 转成 stats-update SSE 下发前端。不改 bridge 现有逻辑——只加一个接收 case。

采集：步数、LLM 耗时、输入/输出 token、缓存命中。会话级累计。
首 token 延迟和 tok/s 需要流式 callback，后续再接入。
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

logger = logging.getLogger(__name__)


class SessionStatsMiddleware(AgentMiddleware[AgentState]):
    """会话级统计：步数/LLM 耗时/token/缓存。会话级累计。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._stats: dict[str, int | float] = {
            "turns": 0,
            "steps": 0,
            "llm_ms": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }

    def _extract_usage(self, response: Any) -> dict[str, Any] | None:
        """从 ModelResponse / AIMessage 提取 usage_metadata。"""
        messages = getattr(response, "messages", None)
        if messages and len(messages) > 0:
            msg = messages[0]
        elif isinstance(response, AIMessage):
            msg = response
        else:
            return None
        if not isinstance(msg, AIMessage):
            return None
        usage = getattr(msg, "usage_metadata", None)
        if not isinstance(usage, dict):
            return None
        return usage

    def _record_step(self, response: Any, llm_ms: float) -> None:
        """记录一次模型调用的指标。"""
        self._stats["steps"] = self._stats["steps"] + 1
        self._stats["llm_ms"] = self._stats["llm_ms"] + llm_ms

        usage = self._extract_usage(response)
        if usage:
            self._stats["input_tokens"] = self._stats["input_tokens"] + int(
                usage.get("input_tokens") or 0
            )
            self._stats["output_tokens"] = self._stats["output_tokens"] + int(
                usage.get("output_tokens") or 0
            )
            input_details = usage.get("input_token_details") or {}
            self._stats["cache_read_tokens"] = self._stats["cache_read_tokens"] + int(
                input_details.get("cache_read") or 0
            )
            self._stats["cache_write_tokens"] = self._stats["cache_write_tokens"] + int(
                input_details.get("cache_write") or 0
            )

        # 轮数：简化版——每个 step 视为一个 turn
        self._stats["turns"] = self._stats["steps"]

    def _emit_stats(self) -> None:
        """发 noesis_stats_update custom event。"""
        try:
            from langgraph.config import get_stream_writer
            from langchain_core.callbacks import dispatch_custom_event

            payload = {"type": "noesis_stats_update", **self._stats}
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

            payload = {"type": "noesis_stats_update", **self._stats}
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
        self._record_step(response, llm_ms)
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
        self._record_step(response, llm_ms)
        await self._aemit_stats()
        return response


__all__ = ["SessionStatsMiddleware"]
