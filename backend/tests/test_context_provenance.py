"""Context provenance contracts (task 2.1).

Provenance lets capability middleware record token source attribution without
that metadata leaking into the Provider wire payload.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import HumanMessage, SystemMessage

from noesis.agents.middlewares.kernel.context_metrics import build_context_snapshot_from_request
from noesis.runtime.context_provenance import (
    ContextProvenance,
    current_context_provenance,
    estimate_source_tokens,
    get_or_create_context_provenance,
    reset_context_provenance,
)


def _runtime_with_thread(thread_id: str) -> MagicMock:
    runtime = MagicMock()
    runtime.execution_info = MagicMock(thread_id=thread_id, run_id="run-prov")
    return runtime


def test_provenance_records_and_snapshots_sources() -> None:
    """add 累计同源 token；snapshot 返回独立副本。"""
    reset_context_provenance()
    try:
        prov = get_or_create_context_provenance()
        prov.add("skills", 900)
        prov.add("skills", 100)  # 同源累计
        prov.add("memory", 300)

        snap = prov.snapshot()
        assert snap == {"skills": 1000, "memory": 300}
        # snapshot 是副本，改原不影响
        prov.add("rag", 50)
        assert "rag" not in snap
    finally:
        reset_context_provenance()


def test_provenance_ignores_empty_and_nonpositive() -> None:
    """空名或非正 token 不记录。"""
    reset_context_provenance()
    try:
        prov = get_or_create_context_provenance()
        prov.add("", 100)
        prov.add("skills", 0)
        prov.add("skills", -5)
        assert prov.snapshot() == {}
    finally:
        reset_context_provenance()


def test_provenance_is_request_scoped_and_resets() -> None:
    """reset 后 current_context_provenance 返回 None，新请求得到全新实例。"""
    reset_context_provenance()
    assert current_context_provenance() is None

    prov_a = get_or_create_context_provenance()
    prov_a.add("skills", 900)
    assert current_context_provenance() is prov_a

    reset_context_provenance()
    assert current_context_provenance() is None

    prov_b = get_or_create_context_provenance()
    assert prov_b is not prov_a
    assert prov_b.snapshot() == {}


def test_estimate_source_tokens_handles_string_and_nonstring() -> None:
    """estimate_source_tokens 对字符串/非字符串内容给出正值，空内容返回 0。"""
    assert estimate_source_tokens(None) == 0
    assert estimate_source_tokens("") == 0
    assert estimate_source_tokens("   ") == 0
    assert estimate_source_tokens("system prompt " * 50) > 0
    # 非字符串（dict/list/metadata）能 stringify 后估算
    assert estimate_source_tokens({"name": "skill", "desc": "x" * 200}) > 0


def test_context_snapshot_consumes_provenance_sources() -> None:
    """build_context_snapshot_from_request 读 provenance 填 sources；无 provenance 时 sources 为空。"""
    reset_context_provenance()
    model = MagicMock()
    model.get_num_tokens.side_effect = RuntimeError("no tokenizer")
    request = ModelRequest(
        model=model,
        system_message=SystemMessage(content="sys prompt"),
        messages=[HumanMessage(content="用户问题")],
        runtime=_runtime_with_thread("sess-prov"),
    )
    with patch("noesis.agents.middlewares.kernel.context_metrics.resolve_context_max_tokens", return_value=128000):
        # 无 provenance
        snap = build_context_snapshot_from_request(request)
        assert snap["sources"] == {}

        # 有 provenance
        prov = get_or_create_context_provenance()
        prov.add("skills", 900)
        prov.add("memory", 300)
        snap2 = build_context_snapshot_from_request(request)
        assert snap2["sources"] == {"skills": 900, "memory": 300}
    reset_context_provenance()


def test_provenance_does_not_leak_into_provider_wire_payload() -> None:
    """provenance 是 request-scoped ContextVar，不进 ModelRequest 序列化字段，不污染 Provider payload。

    断言：即使 provenance 存在，_get_request_payload 生成的 messages/tools 不含
    Noesis provenance 字段（noesis_provenance / sources / estimated 等）。
    """
    from noesis.llm.factory import build_chat_model

    model = build_chat_model(
        model_type="opencode",
        model_name="deepseek-v4-flash-free",
        temperature=0.0,
        model_base_url="https://opencode.ai/zen/v1",
        model_api_key="public",
    )
    reset_context_provenance()
    try:
        prov = get_or_create_context_provenance()
        prov.add("skills", 900)
        prov.add("memory", 300)

        from langchain_core.messages import HumanMessage
        payload = model._get_request_payload(  # noqa: SLF001
            [HumanMessage(content="hi"), SystemMessage(content="sys")]
        )
        payload_str = str(payload)
        # Noesis 内部 provenance 调试字段 SHALL NOT 出现在 wire payload
        assert "noesis_provenance" not in payload_str
        assert "estimated" not in payload_str
        assert "counting_method" not in payload_str
        assert "900" not in payload_str  # provenance token 数不进 payload
    finally:
        reset_context_provenance()


def test_missing_provenance_keeps_content_in_parent_category() -> None:
    """无 provenance 标记的内容留在父分类（system/tool_results），不强行归到 Skills。"""
    reset_context_provenance()
    model = MagicMock()
    model.get_num_tokens.side_effect = RuntimeError("no tokenizer")
    request = ModelRequest(
        model=model,
        system_message=SystemMessage(content="sys prompt " * 20),
        messages=[HumanMessage(content="问题")],
        runtime=_runtime_with_thread("sess-no-prov"),
    )
    with patch("noesis.agents.middlewares.kernel.context_metrics.resolve_context_max_tokens", return_value=128000):
        snap = build_context_snapshot_from_request(request)

    # 无 provenance → sources 空，但 system 分类仍有值（内容留在 system 父分类）
    assert snap["sources"] == {}
    assert snap["breakdown"]["system"] > 0
    reset_context_provenance()


# ---------- task 2.5: provenance 不改变 prompt/tool schema/Provider 请求字段 ----------


def _assert_no_provenance_in_payload(payload: dict) -> None:
    """断言 wire payload 不含 Noesis provenance 调试字段。"""
    payload_str = str(payload)
    for forbidden in ("noesis_provenance", "estimated", "counting_method", "sources", "breakdown", "caller"):
        assert forbidden not in payload_str, f"provenance field '{forbidden}' leaked into payload"


def test_skills_provenance_does_not_leak_into_payload() -> None:
    """Skills system 注入标 provenance 后，system prompt 文本与 wire payload 不含 Noesis 字段。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    from noesis.llm.factory import build_chat_model
    from noesis.runtime.context_provenance import get_or_create_context_provenance

    model = build_chat_model(
        model_type="opencode",
        model_name="deepseek-v4-flash-free",
        temperature=0.0,
        model_base_url="https://opencode.ai/zen/v1",
        model_api_key="public",
    )
    reset_context_provenance()
    try:
        prov = get_or_create_context_provenance()
        prov.add("skills", 1500)  # 模拟 Skills 注入后的 provenance 标记

        payload = model._get_request_payload(  # noqa: SLF001
            [SystemMessage(content="base system with skills section"), HumanMessage(content="hi")]
        )
        _assert_no_provenance_in_payload(payload)
        # system prompt 文本不被 provenance 改变
        sys_content = str(payload["messages"][0].get("content", ""))
        assert "1500" not in sys_content  # provenance token 数不进 prompt
    finally:
        reset_context_provenance()


def test_memory_provenance_does_not_leak_into_payload() -> None:
    """Memory 注入标 provenance 后，wire payload 不含 Noesis 字段。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    from noesis.llm.factory import build_chat_model
    from noesis.runtime.context_provenance import get_or_create_context_provenance

    model = build_chat_model(
        model_type="opencode",
        model_name="deepseek-v4-flash-free",
        temperature=0.0,
        model_base_url="https://opencode.ai/zen/v1",
        model_api_key="public",
    )
    reset_context_provenance()
    try:
        prov = get_or_create_context_provenance()
        prov.add("memory", 400)

        payload = model._get_request_payload(
            [SystemMessage(content="system with memory block"), HumanMessage(content="hi")]
        )
        _assert_no_provenance_in_payload(payload)
    finally:
        reset_context_provenance()


def test_rag_and_attachments_provenance_do_not_leak_into_payload() -> None:
    """RAG 工具结果与附件 provenance 标记后，tool schema 与消息 payload 不含 Noesis 字段。"""
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    from langchain_core.tools import tool as lc_tool

    from noesis.llm.factory import build_chat_model
    from noesis.runtime.context_provenance import get_or_create_context_provenance

    @lc_tool
    def search_knowledge_base(query: str) -> str:
        """Search the knowledge base."""
        return query

    model = build_chat_model(
        model_type="opencode",
        model_name="deepseek-v4-flash-free",
        temperature=0.0,
        model_base_url="https://opencode.ai/zen/v1",
        model_api_key="public",
    )
    reset_context_provenance()
    try:
        prov = get_or_create_context_provenance()
        prov.add("rag", 800)
        prov.add("attachments", 200)

        payload = model._get_request_payload(
            [
                SystemMessage(content="sys"),
                HumanMessage(content="问题 with attachments"),
                ToolMessage(content="rag result", tool_call_id="c1", name="search_knowledge_base"),
            ],
            tools=[search_knowledge_base],
        )
        _assert_no_provenance_in_payload(payload)
        # tool schema 不被 provenance 改变
        if "tools" in payload:
            tools_str = str(payload["tools"])
            assert "noesis_provenance" not in tools_str
            assert "800" not in tools_str  # rag provenance 数不进 tool schema
    finally:
        reset_context_provenance()


def test_provenance_does_not_alter_prompt_text() -> None:
    """provenance 标记不改变 prompt 文本本身（不插分隔符、不附加调试文本）。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    from noesis.llm.factory import build_chat_model
    from noesis.runtime.context_provenance import get_or_create_context_provenance

    model = build_chat_model(
        model_type="opencode",
        model_name="deepseek-v4-flash-free",
        temperature=0.0,
        model_base_url="https://opencode.ai/zen/v1",
        model_api_key="public",
    )
    base_system = "You are a helpful assistant."
    reset_context_provenance()
    try:
        # 无 provenance 的 baseline payload
        payload_base = model._get_request_payload(
            [SystemMessage(content=base_system), HumanMessage(content="hi")]
        )
        base_sys = payload_base["messages"][0].get("content")

        # 有 provenance 的 payload
        prov = get_or_create_context_provenance()
        prov.add("skills", 900)
        prov.add("memory", 300)
        prov.add("rag", 500)
        payload_prov = model._get_request_payload(
            [SystemMessage(content=base_system), HumanMessage(content="hi")]
        )
        prov_sys = payload_prov["messages"][0].get("content")

        # prompt 文本完全一致（provenance 不改 prompt）
        assert base_sys == prov_sys == base_system
    finally:
        reset_context_provenance()
