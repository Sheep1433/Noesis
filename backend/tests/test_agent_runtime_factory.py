"""Agent runtime factory inventory and ordering contracts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, ToolMessage

from noesis.factory import build_noesis_runtime_middleware
from noesis.middlewares.kernel.context_lifecycle_middleware import ContextLifecycleMiddleware


def test_runtime_stack_contains_only_the_five_kernel_owners() -> None:
    config = SimpleNamespace(
        context_display_enabled=False,
        summarization_enabled=False,
        max_retries=0,
    )
    with patch("noesis.factory.ModelConfig", config):
        stack = build_noesis_runtime_middleware()

    assert [type(item).__name__ for item in stack] == [
        "RuntimeTelemetryMiddleware",
        "ToolExecutionMiddleware",
        "RunGovernorMiddleware",
        "ContextLifecycleMiddleware",
        "ModelExecutionMiddleware",
    ]


def test_context_normalization_does_not_mutate_persisted_parts_shape() -> None:
    persisted = {
        "version": 1,
        "parts": [{"type": "tool", "tool_call_id": "call_1", "status": "streaming"}],
    }
    messages = [
        AIMessage(content="", tool_calls=[{"name": "bash", "id": "call_1", "args": {}}])
    ]

    normalized = ContextLifecycleMiddleware.normalize_messages(messages)

    assert isinstance(normalized[-1], ToolMessage)
    assert normalized[-1].tool_call_id == "call_1"
    assert persisted == {
        "version": 1,
        "parts": [{"type": "tool", "tool_call_id": "call_1", "status": "streaming"}],
    }
