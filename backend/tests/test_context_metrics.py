"""Context window metrics utilities."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from agent.middlewares.context_metrics import (
    build_context_snapshot,
    build_context_snapshot_from_request,
    compute_used_percentage,
)
from agent.middlewares.context_metrics_middleware import (
    ContextMetricsMiddleware,
    ContextMetricsRegistry,
    resolve_session_id_for_request,
)
from llm.model_limits import DEFAULT_CONTEXT_TOKENS, resolve_context_max_tokens


def _runtime_with_thread(thread_id: str) -> MagicMock:
    runtime = MagicMock()
    runtime.execution_info = MagicMock(thread_id=thread_id)
    return runtime


@patch("llm.catalog.resolve_catalog_entry")
def test_resolve_context_max_tokens_from_global_config(mock_resolve) -> None:
    from llm.catalog import ModelCatalogEntry

    mock_resolve.return_value = ModelCatalogEntry(
        id="default",
        label="Default",
        model_type="qwen",
        model_name="qwen-plus",
        temperature=0.7,
        base_url="https://example.com/v1",
        limit=None,
    )
    cfg = SimpleNamespace(context_max_input_tokens=64000)
    with patch("llm.model_limits.ModelConfig", cfg):
        assert resolve_context_max_tokens() == 64000


def test_compute_used_percentage_minimum_one_when_nonzero() -> None:
    assert compute_used_percentage(630, 128_000) == 1
    assert compute_used_percentage(0, 128_000) == 0
    assert compute_used_percentage(68_000, 128_000) == 53


@patch("llm.catalog.resolve_catalog_entry")
def test_resolve_context_max_tokens_default_when_unset(mock_resolve) -> None:
    from llm.catalog import ModelCatalogEntry

    mock_resolve.return_value = ModelCatalogEntry(
        id="default",
        label="Default",
        model_type="qwen",
        model_name="qwen-plus",
        temperature=0.7,
        base_url="https://example.com/v1",
        limit=None,
    )
    cfg = SimpleNamespace(context_max_input_tokens=0)
    with patch("llm.model_limits.ModelConfig", cfg):
        assert resolve_context_max_tokens() == DEFAULT_CONTEXT_TOKENS


@patch("agent.middlewares.context_metrics.resolve_context_max_tokens", return_value=1000)
def test_build_context_snapshot_percentage(mock_resolve) -> None:
    messages = [SystemMessage(content="x" * 4000), HumanMessage(content="y" * 4000)]
    snap = build_context_snapshot(messages, model_id="flash")
    assert snap["max_tokens"] == 1000
    assert snap["current_tokens"] > 0
    assert 0 <= snap["used_percentage"] <= 100
    mock_resolve.assert_called_once_with("flash")


def test_build_context_snapshot_from_request_includes_system_and_tools() -> None:
    @tool
    def demo_search(query: str) -> str:
        """Search the knowledge base for relevant documents."""
        return query

    model = MagicMock()
    model.get_num_tokens.return_value = 900
    request = ModelRequest(
        model=model,
        system_message=SystemMessage(content="system prompt " * 50),
        messages=[HumanMessage(content="你好")],
        tools=[demo_search],
        runtime=_runtime_with_thread("sess-tools"),
    )
    with patch("agent.middlewares.context_metrics.resolve_context_max_tokens", return_value=128000) as mock_resolve:
        snap = build_context_snapshot_from_request(request, model_id="nemotron")
    assert snap["current_tokens"] == 900
    mock_resolve.assert_called_once_with("nemotron")
    model.get_num_tokens.assert_called_once()
    payload = model.get_num_tokens.call_args[0][0]
    assert "system prompt" in payload
    assert "demo_search" in payload


def test_resolve_session_id_from_execution_info() -> None:
    request = ModelRequest(
        model=MagicMock(),
        messages=[HumanMessage(content="hello")],
        runtime=_runtime_with_thread("sess-from-thread"),
    )
    assert resolve_session_id_for_request(request) == "sess-from-thread"


def test_resolve_session_id_missing_execution_info() -> None:
    runtime = MagicMock()
    runtime.execution_info = None
    request = ModelRequest(
        model=MagicMock(),
        messages=[HumanMessage(content="hello")],
        runtime=runtime,
    )
    assert resolve_session_id_for_request(request) == ""


def test_context_metrics_middleware_records_registry() -> None:
    cfg = SimpleNamespace(context_display_enabled=True)
    mw = ContextMetricsMiddleware(model_id="flash")
    model = MagicMock()
    model.get_num_tokens.return_value = 512
    request = ModelRequest(
        model=model,
        system_message=SystemMessage(content="noesis system"),
        messages=[HumanMessage(content="hello world")],
        tools=[],
        runtime=_runtime_with_thread("sess-ctx-1"),
    )
    with (
        patch("agent.middlewares.context_metrics_middleware.ModelConfig", cfg),
        patch("agent.middlewares.context_metrics.resolve_context_max_tokens", return_value=200_000),
    ):
        mw.wrap_model_call(request, lambda req: MagicMock())
    snap = ContextMetricsRegistry.peek("sess-ctx-1")
    assert snap is not None
    assert snap["current_tokens"] == 512
    assert snap["max_tokens"] == 200_000
    ContextMetricsRegistry.clear("sess-ctx-1")


def test_context_metrics_middleware_skips_when_display_disabled() -> None:
    cfg = SimpleNamespace(context_display_enabled=False)
    mw = ContextMetricsMiddleware(model_id="flash")
    request = ModelRequest(
        model=MagicMock(),
        messages=[HumanMessage(content="hello")],
        runtime=_runtime_with_thread("sess-ctx-2"),
    )
    with patch("agent.middlewares.context_metrics_middleware.ModelConfig", cfg):
        mw.wrap_model_call(request, lambda req: MagicMock())
    assert ContextMetricsRegistry.peek("sess-ctx-2") is None
