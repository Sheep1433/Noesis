"""Context window metrics utilities."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from noesis.middlewares.kernel.context_metrics import (
    BREAKDOWN_KEYS,
    DEFAULT_CONTEXT_CALLER,
    METHOD_APPROXIMATE,
    METHOD_MODEL_TOKENIZER,
    build_context_snapshot,
    build_context_snapshot_from_request,
    build_request_breakdown,
    compute_used_percentage,
)
from noesis.middlewares.observability.context_metrics_middleware import (
    ContextMetricsMiddleware,
    resolve_run_id_for_request,
    resolve_session_id_for_request,
)
from noesis.middlewares.observability.context_metrics_registry import ContextMetricsRegistry
from noesis.llm.model_limits import DEFAULT_CONTEXT_TOKENS, resolve_context_max_tokens


def _runtime_with_thread(thread_id: str) -> MagicMock:
    runtime = MagicMock()
    runtime.execution_info = MagicMock(thread_id=thread_id)
    return runtime


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


@patch("noesis.middlewares.kernel.context_metrics.resolve_context_max_tokens")
def test_build_context_snapshot_percentage(mock_require) -> None:
    mock_require.return_value = 1000
    messages = [SystemMessage(content="x" * 4000), HumanMessage(content="y" * 4000)]
    snap = build_context_snapshot(messages, model_id="flash")
    assert snap["max_tokens"] == 1000
    assert snap["current_tokens"] > 0
    assert 0 <= snap["used_percentage"] <= 100
    mock_require.assert_called_once_with("flash")


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
    with patch(
        "noesis.middlewares.kernel.context_metrics.resolve_context_max_tokens"
    ) as mock_require:
        mock_require.return_value = 128000
        snap = build_context_snapshot_from_request(request, model_id="nemotron")
    assert snap["current_tokens"] == 900
    mock_require.assert_called_once_with("nemotron")
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
            patch("noesis.middlewares.observability.context_metrics_middleware.ModelConfig", cfg),
        patch(
            "noesis.middlewares.kernel.context_metrics.resolve_context_max_tokens",
            return_value=200_000,
        ),
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
    with patch("noesis.middlewares.observability.context_metrics_middleware.ModelConfig", cfg):
        mw.wrap_model_call(request, lambda req: MagicMock())
    assert ContextMetricsRegistry.peek("sess-ctx-2") is None


# ---------- task 1.1: context snapshot breakdown / estimated / counting_method / caller ----------


def test_build_request_breakdown_classifies_system_conversation_tool_results() -> None:
    """纯函数把最终请求消息拆为 system/conversation/tool_results/tool_definitions，不依赖 ModelRequest。"""
    system = SystemMessage(content="system prompt " * 20)
    messages = [
        HumanMessage(content="用户问题 " * 10),
        ToolMessage(content="工具结果 " * 30, tool_call_id="call-1", name="search"),
        HumanMessage(content="追问 " * 5),
    ]

    @tool
    def demo_search(query: str) -> str:
        """Search the knowledge base."""
        return query

    breakdown = build_request_breakdown(messages, system, [demo_search])

    assert set(breakdown.keys()) == set(BREAKDOWN_KEYS)
    assert breakdown["system"] > 0
    assert breakdown["conversation"] > 0
    assert breakdown["tool_results"] > 0
    assert breakdown["tool_definitions"] > 0
    # other 在纯函数阶段为 0，由 build_context_snapshot_from_request 填充
    assert breakdown["other"] == 0


def test_build_request_breakdown_handles_no_system_or_no_tools() -> None:
    """缺 system_message 或 tools 时对应分类为 0，不报错。"""
    breakdown = build_request_breakdown(
        [HumanMessage(content="hi")], None, []
    )
    assert breakdown["system"] == 0
    assert breakdown["tool_definitions"] == 0
    assert breakdown["tool_results"] == 0
    assert breakdown["conversation"] > 0


def test_context_snapshot_from_request_has_backward_compatible_fields() -> None:
    """扩展快照保留 current_tokens/max_tokens/used_percentage，旧消费者不受影响。"""
    model = MagicMock()
    model.get_num_tokens.return_value = 900
    request = ModelRequest(
        model=model,
        system_message=SystemMessage(content="system " * 50),
        messages=[HumanMessage(content="你好")],
        runtime=_runtime_with_thread("sess-snap"),
    )
    with patch("noesis.middlewares.kernel.context_metrics.resolve_context_max_tokens", return_value=128000):
        snap = build_context_snapshot_from_request(request, model_id="flash")

    assert snap["current_tokens"] == 900
    assert snap["max_tokens"] == 128000
    assert 0 <= snap["used_percentage"] <= 100
    # 新增字段
    assert snap["estimated"] is True
    assert snap["counting_method"] == METHOD_MODEL_TOKENIZER
    assert snap["caller"] == DEFAULT_CONTEXT_CALLER
    assert isinstance(snap["breakdown"], dict)
    assert isinstance(snap["sources"], dict)


def test_context_snapshot_breakdown_sums_to_current_tokens() -> None:
    """approximate 路径下分类之和 == current_tokens；other 吸收 framing 差值。

    model tokenizer 不可用 → current_tokens 与 breakdown 各分类同用 approximate counter，
    口径一致，other 仅吸收 messages 整体与拆分之间的 framing 差值。
    """
    model = MagicMock()
    model.get_num_tokens.side_effect = RuntimeError("no tokenizer")
    request = ModelRequest(
        model=model,
        system_message=SystemMessage(content="system " * 40),
        messages=[HumanMessage(content="用户问题 " * 20), ToolMessage(content="结果 " * 60, tool_call_id="c1", name="t")],
        runtime=_runtime_with_thread("sess-sum"),
    )
    with patch("noesis.middlewares.kernel.context_metrics.resolve_context_max_tokens", return_value=200000):
        snap = build_context_snapshot_from_request(request)

    assert snap["counting_method"] == METHOD_APPROXIMATE
    breakdown = snap["breakdown"]
    assert sum(breakdown[key] for key in BREAKDOWN_KEYS) == snap["current_tokens"]
    assert breakdown["system"] > 0
    assert breakdown["conversation"] > 0
    assert breakdown["tool_results"] > 0
    assert breakdown["other"] == 0


def test_context_snapshot_counting_method_falls_back_to_approximate() -> None:
    """model.get_num_tokens 不可用或抛错时回退到 approximate 并标记 method。"""
    model = MagicMock()
    model.get_num_tokens.side_effect = RuntimeError("tokenizer unavailable")
    request = ModelRequest(
        model=model,
        system_message=SystemMessage(content="system prompt"),
        messages=[HumanMessage(content="hello world")],
        runtime=_runtime_with_thread("sess-fallback"),
    )
    with patch("noesis.middlewares.kernel.context_metrics.resolve_context_max_tokens", return_value=128000):
        snap = build_context_snapshot_from_request(request)

    assert snap["counting_method"] == METHOD_APPROXIMATE
    assert snap["current_tokens"] > 0
    breakdown = snap["breakdown"]
    assert sum(breakdown[key] for key in BREAKDOWN_KEYS) == snap["current_tokens"]
    assert breakdown["other"] == 0


def test_context_snapshot_caller_override() -> None:
    """caller 可由调用方传入，供 attribution 中间件区分 lead_agent/subagent/middleware。"""
    request = ModelRequest(
        model=MagicMock(),
        messages=[HumanMessage(content="hi")],
        runtime=_runtime_with_thread("sess-caller"),
    )
    with patch("noesis.middlewares.kernel.context_metrics.resolve_context_max_tokens", return_value=128000):
        snap = build_context_snapshot_from_request(request, caller="subagent")
    assert snap["caller"] == "subagent"


def test_context_snapshot_model_tokenizer_other_absorbs_framing() -> None:
    """model tokenizer 路径：current_tokens 用模型 tokenizer，breakdown 用 approximate。

    两条路径口径不同时，other 吸收差值且 SHALL NOT 为负；breakdown 各分类内部一致
    使用 approximate。不强求和 == current_tokens（spec 允许 other 吸收 framing 差值）。
    """
    model = MagicMock()
    model.get_num_tokens.return_value = 500
    request = ModelRequest(
        model=model,
        system_message=SystemMessage(content="s" * 2000),
        messages=[HumanMessage(content="q" * 2000)],
        runtime=_runtime_with_thread("sess-framing"),
    )
    with patch("noesis.middlewares.kernel.context_metrics.resolve_context_max_tokens", return_value=128000):
        snap = build_context_snapshot_from_request(request)

    assert snap["counting_method"] == METHOD_MODEL_TOKENIZER
    breakdown = snap["breakdown"]
    assert breakdown["other"] >= 0
    assert breakdown["system"] > 0
    assert breakdown["conversation"] > 0


# ---------- task 1.3: 未知消息类型 / 无 tokenizer / 复杂 tool schema / 多模态 降级 ----------


def test_breakdown_handles_unknown_message_types_safely() -> None:
    """未知/非标准消息类型不报错，计入 conversation 并给出正值或 0。"""
    from langchain_core.messages import AIMessage

    # AIMessage 带 tool_calls（非 ToolMessage，归类 conversation）
    messages = [
        HumanMessage(content="你好"),
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "t", "args": {"x": 1}}]),
        ToolMessage(content="结果", tool_call_id="c1", name="t"),
    ]
    breakdown = build_request_breakdown(messages, SystemMessage(content="sys"), [])
    assert breakdown["conversation"] > 0
    assert breakdown["tool_results"] > 0
    assert set(breakdown.keys()) == set(BREAKDOWN_KEYS)


def test_breakdown_empty_messages_returns_zeros() -> None:
    """空消息列表 + 无 system + 无 tools：各分类为 0，不报错。"""
    breakdown = build_request_breakdown([], None, [])
    assert breakdown == {k: 0 for k in BREAKDOWN_KEYS}


def test_snapshot_complex_tool_schema_does_not_crash() -> None:
    """复杂/嵌套 tool schema（含 $ref、enum、数组）不破坏估算。"""
    complex_tool = {
        "type": "function",
        "function": {
            "name": "complex_search",
            "description": "搜索" * 50,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "查询词"},
                    "filters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "field": {"type": "string", "enum": ["a", "b", "c"]},
                                "op": {"type": "string", "enum": ["eq", "ne", "gt"]},
                                "value": {"type": ["string", "number", "boolean"]},
                            },
                        },
                    },
                    "options": {"type": "object", "additionalProperties": True},
                },
                "required": ["query"],
            },
        },
    }
    model = MagicMock()
    model.get_num_tokens.side_effect = RuntimeError("no tokenizer")
    request = ModelRequest(
        model=model,
        system_message=SystemMessage(content="sys prompt"),
        messages=[HumanMessage(content="问题")],
        tools=[complex_tool],
        runtime=_runtime_with_thread("sess-complex-tool"),
    )
    with patch("noesis.middlewares.kernel.context_metrics.resolve_context_max_tokens", return_value=128000):
        snap = build_context_snapshot_from_request(request)

    assert snap["counting_method"] == METHOD_APPROXIMATE
    assert snap["current_tokens"] > 0
    assert snap["breakdown"]["tool_definitions"] > 0


def test_snapshot_multimodal_content_estimates_image_at_fixed_cost() -> None:
    """多模态 content（text + image_url）：图片按固定 token 估算，文本正常计数，不报错。"""
    model = MagicMock()
    model.get_num_tokens.side_effect = RuntimeError("no tokenizer")
    multimodal_msg = HumanMessage(
        content=[
            {"type": "text", "text": "请看这张图 " * 20},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="}},
        ]
    )
    request = ModelRequest(
        model=model,
        messages=[multimodal_msg],
        runtime=_runtime_with_thread("sess-multimodal"),
    )
    with patch("noesis.middlewares.kernel.context_metrics.resolve_context_max_tokens", return_value=128000):
        snap = build_context_snapshot_from_request(request)

    assert snap["current_tokens"] > 0
    assert snap["breakdown"]["conversation"] > 0
    # 图片固定成本（count_tokens_approximately 默认 85/image）应被计入 conversation
    assert snap["breakdown"]["conversation"] >= 85


def test_snapshot_model_without_get_num_tokens_method() -> None:
    """model 对象没有 get_num_tokens 属性时安全回退到 approximate。"""
    model = object()  # 无任何 tokenizer 方法
    request = ModelRequest(
        model=model,
        system_message=SystemMessage(content="sys"),
        messages=[HumanMessage(content="hello world")],
        runtime=_runtime_with_thread("sess-no-method"),
    )
    with patch("noesis.middlewares.kernel.context_metrics.resolve_context_max_tokens", return_value=128000):
        snap = build_context_snapshot_from_request(request)

    assert snap["counting_method"] == METHOD_APPROXIMATE
    assert snap["current_tokens"] > 0


def test_breakdown_other_never_negative() -> None:
    """model tokenizer 返回极小值时，other 钳为 0 而非负数。"""
    model = MagicMock()
    model.get_num_tokens.return_value = 1  # 远小于各分类 approximate 之和
    request = ModelRequest(
        model=model,
        system_message=SystemMessage(content="system " * 100),
        messages=[HumanMessage(content="用户问题 " * 100)],
        runtime=_runtime_with_thread("sess-tiny-model"),
    )
    with patch("noesis.middlewares.kernel.context_metrics.resolve_context_max_tokens", return_value=128000):
        snap = build_context_snapshot_from_request(request)

    assert snap["breakdown"]["other"] >= 0


# ---------- task 1.4: registry 按 run/caller 隔离 ----------


def _runtime_with_thread_and_run(thread_id: str, run_id: str) -> MagicMock:
    runtime = MagicMock()
    runtime.execution_info = MagicMock(thread_id=thread_id, run_id=run_id)
    return runtime


def test_registry_isolates_concurrent_runs_in_same_session() -> None:
    """同 session 并发两个 run：各自写入不互相覆盖，按 run_id peek 各取各的。"""
    ContextMetricsRegistry._reset_for_tests()
    try:
        snap_a = {"current_tokens": 1000, "max_tokens": 128000, "used_percentage": 1, "caller": "lead_agent"}
        snap_b = {"current_tokens": 5000, "max_tokens": 128000, "used_percentage": 4, "caller": "subagent"}
        ContextMetricsRegistry.put("sess-concurrent", snap_a, run_id="run-A")
        ContextMetricsRegistry.put("sess-concurrent", snap_b, run_id="run-B")

        got_a = ContextMetricsRegistry.peek("sess-concurrent", run_id="run-A")
        got_b = ContextMetricsRegistry.peek("sess-concurrent", run_id="run-B")
        assert got_a is not None and got_b is not None
        assert got_a["current_tokens"] == 1000
        assert got_b["current_tokens"] == 5000
        assert got_a["caller"] == "lead_agent"
        assert got_b["caller"] == "subagent"
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
    """run 终态清理只清该 run 精确槽；同 session 其他 run 不受影响。

    peek 在精确槽清空后回退到 session 最新（设计行为，保证 SSE 不丢上下文视图），
    但精确槽确实已清：清理 run-1 后，run-2 精确命中仍返回 200，run-1 回退到 run-2。
    """
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


def test_resolve_run_id_from_execution_info() -> None:
    """middleware 能从 ModelRequest.runtime 解析 run_id。"""
    request = ModelRequest(
        model=MagicMock(),
        messages=[HumanMessage(content="hi")],
        runtime=_runtime_with_thread_and_run("sess-run", "run-abc"),
    )
    assert resolve_run_id_for_request(request) == "run-abc"


def test_resolve_run_id_missing_returns_empty() -> None:
    """runtime 无 run_id 时返回空串，registry 退化为 session 级。"""
    runtime = MagicMock()
    runtime.execution_info = MagicMock(thread_id="sess", run_id=None)
    request = ModelRequest(model=MagicMock(), messages=[HumanMessage(content="hi")], runtime=runtime)
    assert resolve_run_id_for_request(request) == ""


def test_context_metrics_middleware_writes_with_run_id() -> None:
    """middleware 写入时携带 run_id，registry 按 (session, run) 隔离存储。"""
    ContextMetricsRegistry._reset_for_tests()
    cfg = SimpleNamespace(context_display_enabled=True)
    mw = ContextMetricsMiddleware(model_id="flash")
    model = MagicMock()
    model.get_num_tokens.return_value = 512
    request = ModelRequest(
        model=model,
        system_message=SystemMessage(content="sys"),
        messages=[HumanMessage(content="hello")],
        tools=[],
        runtime=_runtime_with_thread_and_run("sess-mw-run", "run-mw-1"),
    )
    with (
        patch("noesis.middlewares.observability.context_metrics_middleware.ModelConfig", cfg),
        patch("noesis.middlewares.kernel.context_metrics.resolve_context_max_tokens", return_value=200_000),
    ):
        mw.wrap_model_call(request, lambda req: MagicMock())
    snap = ContextMetricsRegistry.peek("sess-mw-run", run_id="run-mw-1")
    assert snap is not None
    assert snap["current_tokens"] == 512
    assert snap["caller"] == DEFAULT_CONTEXT_CALLER
    ContextMetricsRegistry._reset_for_tests()
