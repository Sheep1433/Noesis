from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from noesis.factory import build_noesis_middleware, middleware_inventory


def test_inventory_is_generated_from_actual_instances() -> None:
    config = SimpleNamespace(summarization_enabled=False, tool_output_max_chars=24_000, max_retries=6)
    with patch("noesis.factory.ModelConfig", config):
        stack = build_noesis_middleware(
            profile="COMMON_QA",
            model=MagicMock(),
        )
    inventory = middleware_inventory(stack)
    assert [entry.name for entry in inventory] == [type(item).__name__ for item in stack]
    assert [entry.order for entry in inventory] == list(range(len(stack)))
    assert [entry.name for entry in inventory] == [
        "ToolResultBudgetMiddleware",
        "ToolFailureMiddleware",
        "DynamicContextMiddleware",
        "PatchToolCallsMiddleware",
        "LLMErrorHandlingMiddleware",
    ]
