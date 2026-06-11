"""Context window metrics utilities."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from langchain_core.messages import HumanMessage, SystemMessage

from agent.middlewares.context_metrics import (
    DEFAULT_CONTEXT_MAX_INPUT_TOKENS,
    build_context_snapshot,
    resolve_context_max_tokens,
)
from agent.middlewares.context_metrics_middleware import (
    ContextMetricsMiddleware,
    ContextMetricsRegistry,
)


def test_resolve_context_max_tokens_prefers_context_config() -> None:
    cfg = SimpleNamespace(context_max_input_tokens=64000, summarization_max_input_tokens=8000)
    with patch("agent.middlewares.context_metrics.ModelConfig", cfg):
        assert resolve_context_max_tokens() == 64000


def test_resolve_context_max_tokens_default_when_unset() -> None:
    cfg = SimpleNamespace(context_max_input_tokens=0, summarization_max_input_tokens=0)
    mock_model = MagicMock()
    mock_model.profile = None
    with (
        patch("agent.middlewares.context_metrics.ModelConfig", cfg),
        patch("agent.middlewares.context_metrics.get_llm", return_value=mock_model),
    ):
        assert resolve_context_max_tokens() == DEFAULT_CONTEXT_MAX_INPUT_TOKENS


def test_build_context_snapshot_percentage() -> None:
    cfg = SimpleNamespace(context_max_input_tokens=1000, summarization_max_input_tokens=0)
    messages = [SystemMessage(content="x" * 4000), HumanMessage(content="y" * 4000)]
    with patch("agent.middlewares.context_metrics.ModelConfig", cfg):
        snap = build_context_snapshot(messages)
    assert snap["max_tokens"] == 1000
    assert snap["current_tokens"] > 0
    assert 0 <= snap["used_percentage"] <= 100


def test_context_metrics_middleware_records_registry() -> None:
    cfg = SimpleNamespace(context_display_enabled=True)
    mw = ContextMetricsMiddleware()
    state = {"messages": [HumanMessage(content="hello world")]}
    runtime = MagicMock()
    runtime.config = {"configurable": {"thread_id": "sess-ctx-1"}}
    with patch("agent.middlewares.context_metrics_middleware.ModelConfig", cfg):
        mw.before_model(state, runtime)
    snap = ContextMetricsRegistry.peek("sess-ctx-1")
    assert snap is not None
    assert snap["max_tokens"] > 0
    ContextMetricsRegistry.clear("sess-ctx-1")


def test_context_metrics_middleware_skips_when_display_disabled() -> None:
    cfg = SimpleNamespace(context_display_enabled=False)
    mw = ContextMetricsMiddleware()
    state = {"messages": [HumanMessage(content="hello")]}
    runtime = MagicMock()
    runtime.config = {"configurable": {"thread_id": "sess-ctx-2"}}
    with patch("agent.middlewares.context_metrics_middleware.ModelConfig", cfg):
        mw.before_model(state, runtime)
    assert ContextMetricsRegistry.peek("sess-ctx-2") is None
