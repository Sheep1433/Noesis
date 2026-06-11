"""Emit session context occupancy before each LLM call."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from threading import Lock
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime
from typing_extensions import override

from agent.middlewares.context_metrics import build_context_snapshot
from config.env import ModelConfig
from utils.log_util import logger


class ContextMetricsRegistry:
    """Thread-scoped latest context snapshot (thread_id == session_id)."""

    _lock = Lock()
    _store: dict[str, dict[str, int]] = {}

    @classmethod
    def put(cls, thread_id: str, snapshot: dict[str, int]) -> None:
        if not thread_id:
            return
        with cls._lock:
            cls._store[thread_id] = dict(snapshot)

    @classmethod
    def pop(cls, thread_id: str) -> dict[str, int] | None:
        if not thread_id:
            return None
        with cls._lock:
            return cls._store.pop(thread_id, None)

    @classmethod
    def peek(cls, thread_id: str) -> dict[str, int] | None:
        if not thread_id:
            return None
        with cls._lock:
            snap = cls._store.get(thread_id)
            return dict(snap) if snap else None

    @classmethod
    def clear(cls, thread_id: str) -> None:
        if not thread_id:
            return
        with cls._lock:
            cls._store.pop(thread_id, None)


def _thread_id_from_runtime(runtime: Runtime) -> str:
    """与 LoopDetectionMiddleware 一致：thread_id 在 runtime.context 中。"""
    try:
        ctx = runtime.context or {}
        thread_id = ctx.get("thread_id")
        if thread_id:
            return str(thread_id)
    except Exception:
        pass
    return ""


def _try_emit_stream_writer(snapshot: dict[str, int]) -> None:
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    try:
        writer({"type": "context-update", "context": snapshot})
    except Exception:
        logger.debug("context-update custom stream 写入失败", exc_info=True)


class ContextMetricsMiddleware(AgentMiddleware):
    """Record context fill level on each model call (after SessionClock patches)."""

    def _record(self, request: ModelRequest) -> None:
        if not ModelConfig.context_display_enabled:
            return
        snapshot = build_context_snapshot(list(request.messages))
        thread_id = _thread_id_from_runtime(request.runtime)
        ContextMetricsRegistry.put(thread_id, snapshot)
        _try_emit_stream_writer(snapshot)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        self._record(request)
        return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        self._record(request)
        return await handler(request)
