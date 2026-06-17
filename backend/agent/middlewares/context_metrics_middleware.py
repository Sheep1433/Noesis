"""Emit session context occupancy before each LLM call."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from threading import Lock
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from typing_extensions import override

from agent.middlewares.context_metrics import build_context_snapshot_from_request
from config.env import ModelConfig
from common.logging import logger


class ContextMetricsRegistry:
    """按 session_id 暂存最近一次上下文快照，供 SSE bridge 在 usage-update 时读取。"""

    _lock = Lock()
    _store: dict[str, dict[str, int]] = {}

    @classmethod
    def put(cls, session_id: str, snapshot: dict[str, int]) -> None:
        if not session_id:
            return
        with cls._lock:
            cls._store[session_id] = dict(snapshot)

    @classmethod
    def peek(cls, session_id: str) -> dict[str, int] | None:
        if not session_id:
            return None
        with cls._lock:
            snap = cls._store.get(session_id)
            return dict(snap) if snap else None

    @classmethod
    def clear(cls, session_id: str) -> None:
        if not session_id:
            return
        with cls._lock:
            cls._store.pop(session_id, None)


def resolve_session_id_for_request(request: ModelRequest) -> str:
    """会话键 = LangGraph execution_info.thread_id（Agent 配置为 Noesis session_id）。"""
    runtime = request.runtime
    if runtime is None:
        return ""
    exec_info = getattr(runtime, "execution_info", None)
    if exec_info is None:
        return ""
    thread_id = getattr(exec_info, "thread_id", None)
    return str(thread_id) if thread_id else ""


class ContextMetricsMiddleware(AgentMiddleware):
    """Record context fill level immediately before model invoke (final ModelRequest)."""

    def _record(self, request: ModelRequest) -> None:
        if not ModelConfig.context_display_enabled:
            return
        session_id = resolve_session_id_for_request(request)
        if not session_id:
            logger.warning(
                "[context_metrics] runtime.execution_info.thread_id 缺失，跳过上下文快照写入"
            )
            return
        ContextMetricsRegistry.put(session_id, build_context_snapshot_from_request(request))

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
