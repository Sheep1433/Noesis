"""评测 LLM 调用级 Langfuse 过程记录测试（fake，零外部依赖）。"""

import json

from langchain_core.messages import AIMessage, HumanMessage

from evals.compression.__main__ import (
    _MAX_MESSAGES,
    _output_payload,
    _record_generation,
    _truncate_messages,
)


class _Resp:
    content = "回答内容"


class _ToolResp:
    content = ""
    tool_calls = [{"name": "search_history", "args": {"query": "q"},
                   "id": "c1", "type": "tool_call"}]


class _Inner:
    model_name = "test-model"


def test_truncate_messages_keeps_roles_and_structure():
    msgs = [HumanMessage(content="问题"), AIMessage(content="回答")]
    out = _truncate_messages(msgs)
    assert out == [
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "回答"},
    ]
    # 非列表输入包装为单条 user 消息
    assert _truncate_messages("裸文本") == [
        {"role": "user", "content": "裸文本"}]


def test_truncate_messages_clips_long_content_per_message():
    big = "A" * 5000
    out = _truncate_messages([HumanMessage(content=big)])
    assert "[截断：原文 5,000 字符]" in out[0]["content"]
    assert len(out[0]["content"]) < 1500


def test_truncate_messages_sampling_when_too_many():
    msgs = [HumanMessage(content=f"m{i}") for i in range(_MAX_MESSAGES + 100)]
    out = _truncate_messages(msgs)
    assert len(out) == _MAX_MESSAGES + 1  # 首尾各半 + 1 条省略标记
    assert any("省略" in str(m["content"]) for m in out)
    assert out[0]["content"] == "m0"
    assert out[-1]["content"] == f"m{_MAX_MESSAGES + 99}"


def test_output_payload_plain_text():
    assert _output_payload(_Resp()) == "回答内容"


def test_output_payload_tool_call_round_is_authentic():
    """调工具轮对齐 LangChain AIMessage 真实形态：content 原样（空就空），
    不造占位文本冒充模型输出；tool_calls 完整含 id/type。"""
    out = _output_payload(_ToolResp())
    assert out["content"] == ""  # 模型真实输出就是空文本 + 结构化 tool_calls
    assert out["tool_calls"][0] == {
        "name": "search_history", "args": {"query": "q"},
        "id": "c1", "type": "tool_call"}


def test_output_payload_empty_content_is_empty_string():
    class Empty:
        content = ""

    assert _output_payload(Empty()) == ""  # 诚实记录：空就是空


def test_record_generation_passes_structured(monkeypatch):
    captured = {}

    def fake_record(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("server.langfuse.record_eval_generation", fake_record)
    wrapper = type("W", (), {
        "inner": _Inner(),
        "_counters": {"last_in": 747_114, "last_out": 5_220},
    })()
    _record_generation(wrapper, [HumanMessage(content="问题")], _Resp())
    assert captured["name"] == "llm/test-model"
    assert captured["input_messages"] == [{"role": "user", "content": "问题"}]
    assert captured["output_payload"] == "回答内容"
    assert captured["usage"] == {"input": 747_114, "output": 5_220}


def test_record_generation_name_carries_probe_label(monkeypatch):
    """作答/判卷期间 generation 名带当前题号：Langfuse 里可按题定位调用。"""
    import evals.compression.__main__ as main_mod

    captured = {}

    def fake_record(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("server.langfuse.record_eval_generation", fake_record)
    wrapper = type("W", (), {"inner": _Inner(), "_counters": {}})()
    tok = main_mod._current_probe_id.set("p03")
    try:
        _record_generation(wrapper, [HumanMessage(content="问题")], _Resp())
    finally:
        main_mod._current_probe_id.reset(tok)
    assert captured["name"] == "llm/test-model [p03]"


def test_record_generation_swallows_errors(monkeypatch):
    def boom(**_kwargs):
        raise RuntimeError("langfuse down")

    monkeypatch.setattr("server.langfuse.record_eval_generation", boom)
    wrapper = type("W", (), {"inner": _Inner(), "_counters": {}})()
    _record_generation(wrapper, "p", _Resp())  # 不抛即通过


def test_record_eval_tool_span_uses_tool_type():
    """工具执行记 as_type="tool"（SDK Literal 小写同款约束）。"""
    import inspect
    import server.langfuse as lf
    src = inspect.getsource(lf.record_eval_tool_span)
    assert 'as_type="tool"' in src


def test_record_eval_generation_uses_lowercase_type():
    """SDK 仅认小写 "generation"；大写会静默降级为 span（GENERATION 查询为 0 的回归）。"""
    import inspect
    import server.langfuse as lf
    src = inspect.getsource(lf.record_eval_generation)
    assert 'as_type="generation"' in src


def test_record_eval_generation_noop_outside_eval_context():
    from server.langfuse import record_eval_generation

    record_eval_generation(  # 无 eval 上下文时应静默返回（不抛、不建客户端）
        name="llm/x", input_messages=[], output_payload="o",
        usage={"input": 1, "output": 1}, model="m")


def test_activate_eval_langfuse_binds_runtime_deps(monkeypatch):
    """评测激活块内必须绑定 deps 回调：否则走 stream.py 的 Agent 评测线
    （deepresearch/rag/memory）里 langfuse_tracing_enabled() 恒为 False，
    CallbackHandler 永远不会挂上，逐调用 trace 静默丢失。"""
    from types import SimpleNamespace

    from noesis.runtime.deps import (
        langfuse_tracing_enabled,
        merge_langfuse_runnable_config,
    )
    from server.langfuse import activate_eval_langfuse

    settings = SimpleNamespace(
        tracing_enabled=True,
        public_key="pk-lf-test",
        secret_key="sk-lf-test",
        base_url="http://127.0.0.1:1",  # 不可达即可：本测试不发数据
    )
    with activate_eval_langfuse(
        settings=settings, line="agent", tag="t",
        session_id="eval-bind-check-0123456789abcdef",
        trace_id="eval-bind-check-0123456789abcdef",
    ):
        assert langfuse_tracing_enabled() is True
        cfg = merge_langfuse_runnable_config(
            {"configurable": {}},
            langfuse_session_id="eval-bind-check-0123456789abcdef",
            qa_type="SUPER_AGENT_QA",
            enabled=True,
        )
        assert cfg.get("callbacks"), "评测激活块内回调补丁未生效"
        assert cfg["metadata"]["langfuse_session_id"] == "eval-bind-check-0123456789abcdef"
    # 激活块外回落：app 配置未开时 deps 开关必须为 False
    assert langfuse_tracing_enabled() is False
