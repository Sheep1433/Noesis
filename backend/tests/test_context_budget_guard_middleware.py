from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import HumanMessage

from noesis.middlewares.context_budget_guard_middleware import (
    ContextBudgetExceeded,
    ContextBudgetGuardMiddleware,
)


def _request() -> ModelRequest:
    return ModelRequest(
        model=MagicMock(),
        messages=[HumanMessage(content="hello")],
        runtime=MagicMock(),
    )


def test_context_budget_guard_allows_request_within_limit() -> None:
    middleware = ContextBudgetGuardMiddleware(model_id="test")
    handler = MagicMock(return_value="ok")
    with (
        patch(
            "noesis.middlewares.context_budget_guard_middleware.estimate_model_request_input_tokens",
            return_value=900,
        ),
        patch(
            "noesis.middlewares.context_budget_guard_middleware.resolve_context_max_tokens",
            return_value=1000,
        ),
    ):
        assert middleware.wrap_model_call(_request(), handler) == "ok"
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_context_budget_guard_blocks_final_oversized_async_request() -> None:
    middleware = ContextBudgetGuardMiddleware(model_id="test")
    handler = AsyncMock(return_value="unused")
    with (
        patch(
            "noesis.middlewares.context_budget_guard_middleware.estimate_model_request_input_tokens",
            return_value=1001,
        ),
        patch(
            "noesis.middlewares.context_budget_guard_middleware.resolve_context_max_tokens",
            return_value=1000,
        ),
        pytest.raises(ContextBudgetExceeded, match="上下文仍超过模型限制"),
    ):
        await middleware.awrap_model_call(_request(), handler)
    handler.assert_not_awaited()
