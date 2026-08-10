"""Context window metrics utilities."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from noesis.domain.chat.streaming.usage_normalize import compute_used_percentage
from noesis.runtime.observability import ContextMetricsRegistry
from noesis.llm.model_limits import DEFAULT_CONTEXT_TOKENS, resolve_context_max_tokens


@patch("noesis.llm.catalog.resolve_catalog_entry")
def test_resolve_context_max_tokens_from_global_config(mock_resolve) -> None:
    from noesis.llm.catalog import ModelCatalogEntry

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
    with patch("noesis.llm.model_limits.ModelConfig", cfg):
        assert resolve_context_max_tokens() == 64000


def test_compute_used_percentage_minimum_one_when_nonzero() -> None:
    assert compute_used_percentage(630, 128_000) == 1
    assert compute_used_percentage(0, 128_000) == 0
    assert compute_used_percentage(68_000, 128_000) == 53


@patch("noesis.llm.catalog.resolve_catalog_entry")
def test_resolve_context_max_tokens_default_when_unset(mock_resolve) -> None:
    from noesis.llm.catalog import ModelCatalogEntry

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
    with patch("noesis.llm.model_limits.ModelConfig", cfg):
        assert resolve_context_max_tokens() == DEFAULT_CONTEXT_TOKENS


# ---------- registry 按 run/caller 隔离 ----------


def test_registry_isolates_concurrent_runs_in_same_session() -> None:
    """同 session 并发两个 run：各自写入不互相覆盖，按 run_id peek 各取各的。"""
    ContextMetricsRegistry._reset_for_tests()
    try:
        snap_a = {"current_tokens": 1000, "max_tokens": 128000, "used_percentage": 1}
        snap_b = {"current_tokens": 5000, "max_tokens": 128000, "used_percentage": 4}
        ContextMetricsRegistry.put("sess-concurrent", snap_a, run_id="run-A")
        ContextMetricsRegistry.put("sess-concurrent", snap_b, run_id="run-B")

        got_a = ContextMetricsRegistry.peek("sess-concurrent", run_id="run-A")
        got_b = ContextMetricsRegistry.peek("sess-concurrent", run_id="run-B")
        assert got_a is not None and got_b is not None
        assert got_a["current_tokens"] == 1000
        assert got_b["current_tokens"] == 5000
    finally:
        ContextMetricsRegistry._reset_for_tests()


def test_registry_peek_falls_back_to_latest_when_run_id_missing() -> None:
    """无 run_id 或未命中时回退到 session 最新快照（兼容旧调用路径）。"""
    ContextMetricsRegistry._reset_for_tests()
    try:
        snap = {"current_tokens": 2000, "max_tokens": 128000, "used_percentage": 2}
        ContextMetricsRegistry.put("sess-fallback", snap, run_id="run-X")

        # 不传 run_id → 回退到最新
        got = ContextMetricsRegistry.peek("sess-fallback")
        assert got is not None
        assert got["current_tokens"] == 2000
        # 传不存在的 run_id → 也回退到最新
        got2 = ContextMetricsRegistry.peek("sess-fallback", run_id="run-nonexistent")
        assert got2 is not None
        assert got2["current_tokens"] == 2000
    finally:
        ContextMetricsRegistry._reset_for_tests()


def test_registry_clear_run_only_clears_specified_run() -> None:
    """run 终态清理只清该 run 精确槽；同 session 其他 run 不受影响。"""
    ContextMetricsRegistry._reset_for_tests()
    try:
        ContextMetricsRegistry.put("sess-clear", {"current_tokens": 100}, run_id="run-1")
        ContextMetricsRegistry.put("sess-clear", {"current_tokens": 200}, run_id="run-2")

        ContextMetricsRegistry.clear_run("sess-clear", "run-1")

        # run-2 精确命中不受影响
        got_run2 = ContextMetricsRegistry.peek("sess-clear", run_id="run-2")
        assert got_run2 is not None
        assert got_run2["current_tokens"] == 200
        # run-1 精确槽已清，回退到 session 最新（run-2）
        got_run1_fallback = ContextMetricsRegistry.peek("sess-clear", run_id="run-1")
        assert got_run1_fallback is not None
        assert got_run1_fallback["current_tokens"] == 200
    finally:
        ContextMetricsRegistry._reset_for_tests()


def test_registry_clear_session_removes_all_runs() -> None:
    """session 级清理移除该 session 所有 run 快照。"""
    ContextMetricsRegistry._reset_for_tests()
    try:
        ContextMetricsRegistry.put("sess-all", {"current_tokens": 100}, run_id="run-1")
        ContextMetricsRegistry.put("sess-all", {"current_tokens": 200}, run_id="run-2")
        ContextMetricsRegistry.put("sess-all", {"current_tokens": 300}, run_id="")

        ContextMetricsRegistry.clear("sess-all")

        assert ContextMetricsRegistry.peek("sess-all", run_id="run-1") is None
        assert ContextMetricsRegistry.peek("sess-all", run_id="run-2") is None
        assert ContextMetricsRegistry.peek("sess-all") is None
    finally:
        ContextMetricsRegistry._reset_for_tests()
