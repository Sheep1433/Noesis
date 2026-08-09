"""Middleware that records the final model-request context occupancy."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from typing_extensions import override

from noesis.config.env import ModelConfig
from noesis.agents.middlewares.kernel.context_metrics import build_context_snapshot_from_request
from noesis.runtime.observability import ContextMetricsRegistry
from noesis.runtime.logging import logger
from noesis.runtime.thread_context import resolve_runtime_run_id, resolve_runtime_thread_id


def resolve_session_id_for_request(request: ModelRequest) -> str:
    """会话键 = LangGraph execution_info.thread_id。"""
    return resolve_runtime_thread_id(request.runtime)


def resolve_run_id_for_request(request: ModelRequest) -> str:
    """run 键 = LangGraph execution_info.run_id；用于按 run 隔离上下文快照。"""
    return resolve_runtime_run_id(request.runtime)


class ContextMetricsMiddleware(AgentMiddleware):
    """Record context fill level immediately before model invocation."""

    def __init__(self, *, model_id: str | None = None) -> None:
        self._model_id = model_id

    def _record(self, request: ModelRequest) -> None:
        if not ModelConfig.context_display_enabled:
            return
        session_id = resolve_session_id_for_request(request)
        if not session_id:
            logger.warning("[context_metrics] thread_id 缺失，跳过上下文快照写入")
            return
        run_id = resolve_run_id_for_request(request)
        ContextMetricsRegistry.put(
            session_id,
            build_context_snapshot_from_request(request, model_id=self._model_id),
            run_id=run_id,
        )

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
