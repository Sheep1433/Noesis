"""Agent runtime factory inventory and ordering contracts (new flat stack)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from noesis.factory import build_noesis_runtime_middleware


def test_runtime_stack_is_the_new_flat_noesis_baseline() -> None:
    """The COMMON_QA baseline stack is the self-contained Noesis middleware.

    With summarization disabled (no live model config), CompactionMiddleware
    is omitted; the required context-reduction + safety middleware are present
    in design §3 order.
    """
    config = SimpleNamespace(
        context_display_enabled=False,
        summarization_enabled=False,
        max_retries=0,
    )
    with patch("noesis.factory.ModelConfig", config):
        stack = build_noesis_runtime_middleware()

    names = [type(item).__name__ for item in stack]
    expected = [
        "ToolResultBudgetMiddleware",
        "ToolFailureMiddleware",
        "SourceRefreshMiddleware",
        "DynamicContextMiddleware",
        "SnipMiddleware",
        "MicroCompactionMiddleware",
        "PatchToolCallsMiddleware",
        "SafeModelRetryMiddleware",
    ]
    assert names == expected
