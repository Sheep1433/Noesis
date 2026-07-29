"""Reject an oversized final ModelRequest before it reaches the provider."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from typing_extensions import override

from noesis.middlewares.context_metrics import estimate_model_request_input_tokens
from noesis.llm.model_limits import resolve_context_max_tokens


class ContextBudgetExceeded(ValueError):
    """The final request still exceeds the configured model context window."""


class ContextBudgetGuardMiddleware(AgentMiddleware):
    """Validate the final request after summarization and capability middleware."""

    def __init__(self, *, model_id: str | None = None) -> None:
        self._model_id = model_id

    def _check(self, request: ModelRequest) -> None:
        current_tokens = estimate_model_request_input_tokens(request)
        max_tokens = resolve_context_max_tokens(self._model_id)
        if current_tokens > max_tokens:
            raise ContextBudgetExceeded(
                "上下文仍超过模型限制，请缩短输入、减少附件，或开始新的会话"
            )

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        self._check(request)
        return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        self._check(request)
        return await handler(request)
