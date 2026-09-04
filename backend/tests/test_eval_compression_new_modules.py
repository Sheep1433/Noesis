"""压缩评测新模块测试：policies / gen_probes 解析 / export_session / 零 LLM 合成冒烟。"""

import json

import pytest
from langchain_core.messages import HumanMessage

from evals.compression.export_session import extract_messages, scrub
from evals.compression.gen_probes import (
    compacted_region,
    parse_probes_response,
    transcript_sha,
)
from evals.compression.policies import resolve_policy_options
from evals.compression.synthetic import (
    extract_planted_facts,
    fact_dropping_summarize,
    fact_keeping_summarize,
    make_synthetic_fixture,
    make_synthetic_probes,
)


# ---------------------------------------------------------------- policies

def test_resolve_policy_options_merge_and_reject():
    merged = resolve_policy_options({"force": True, "summarization_messages_to_keep": 4}, "aggressive")
    assert merged["summarization_messages_to_keep"] == 2
    assert merged["force"] is True
    with pytest.raises(ValueError, match="未知 policy"):
        resolve_policy_options({}, "nope")


# ---------------------------------------------------------------- gen_probes

def test_compacted_region_keeps_recent():
    messages = [{"type": "human", "content": str(i)} for i in range(10)]
    region = compacted_region(messages, keep_n=3)
    assert len(region) == 7
    assert region[0]["content"] == "0"


def test_parse_probes_response_valid_and_truncates():
    raw = '[{"id": "p1", "question": "q1", "reference_answer": "a1"}, {"question": "q2"}]'
    probes = parse_probes_response(raw, n_questions=1)
    assert len(probes) == 1
    assert probes[0]["id"] == "p1"
    with pytest.raises(ValueError):
        parse_probes_response("not json", n_questions=5)


def test_transcript_sha_stable_and_sensitive():
    msgs = [{"type": "human", "content": "x"}]
    assert transcript_sha(msgs) == transcript_sha([{"content": "x", "type": "human"}])
    assert transcript_sha(msgs) != transcript_sha([{"type": "human", "content": "y"}])


# ---------------------------------------------------------------- export_session

def _assistant_event(blocks, **extra):
    return {"type": "assistant", "message": {"content": blocks}, **extra}


def _user_text_event(text, **extra):
    return {"type": "user", "message": {"role": "user", "content": text}, **extra}


def test_extract_messages_pairs_tool_use_and_result():
    events = [
        _user_text_event("跑一下测试"),
        _assistant_event([
            {"type": "text", "text": "我来执行"},
            {"type": "tool_use", "id": "t1", "name": "terminal", "input": {}},
        ]),
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok output"}
        ]}},
        _assistant_event([{"type": "thinking", "thinking": "internal"}]),
        _user_text_event("<command-name>/clear</command-name>"),
        {"type": "user", "isSidechain": True, "message": {"content": "sidechain"}},
    ]
    messages = extract_messages(iter(events))
    kinds = [m["type"] for m in messages]
    assert kinds == ["human", "ai", "tool"]
    tool_msg = messages[2]
    assert tool_msg["name"] == "terminal"
    assert tool_msg["content"] == "ok output"


def test_scrub_replaces_sensitive_patterns():
    text = "邮箱 a@b.com，key sk-abc123def456ghi789，路径 /Users/zzq/x"
    out = scrub(text)
    assert "a@b.com" not in out and "[EMAIL]" in out
    assert "sk-abc" not in out and "[API_KEY]" in out
    assert "/Users/zzq" not in out


# ---------------------------------------------------------------- 零 LLM 合成冒烟

def test_synthetic_fixture_deterministic_and_facts_planted():
    (fixture, facts), (fixture2, facts2) = make_synthetic_fixture(), make_synthetic_fixture()
    assert fixture == fixture2 and facts == facts2
    assert len(facts) >= 3
    probes = make_synthetic_probes(facts)
    assert len(probes["probes"]) == len(facts)


def test_synthetic_smoke_zero_llm_compression_path(monkeypatch):
    """完整臂流程零 LLM：注入假摘要/作答/判卷，验证压缩机制与 recall 聚合。"""
    from types import SimpleNamespace

    from evals.compression.driver import compress_fixture_messages, parse_fixture_messages
    from evals.compression.grader import answer_probe, grade_probe

    fixture, facts = make_synthetic_fixture()
    messages = parse_fixture_messages(fixture["messages"])

    cfg = SimpleNamespace(
        summarization_enabled=True,
        context_max_input_tokens=8000,
        summarization_trigger_tokens=0,
        summarization_trigger_fraction=0.85,
        summarization_messages_to_keep=4,
    )
    monkeypatch.setattr("evals.compression.driver.ModelConfig", cfg)
    monkeypatch.setattr(
        "noesis.llm.model_limits.ModelConfig", cfg)
    monkeypatch.setattr(
        "evals.compression.driver.resolve_context_max_tokens", lambda: 8000)

    # 保留事实的摘要 → 摘要文本含埋点事实（结构性标记提取）
    result = compress_fixture_messages(messages, compress_options={
        "force": True, "summarize": fact_keeping_summarize})
    assert result["summary_marker_found"] is True
    planted = extract_planted_facts([HumanMessage(content=result["summary_text"])])
    assert planted == facts

    # 丢事实的摘要 → 摘要中零事实
    dropped = compress_fixture_messages(messages, compress_options={
        "force": True, "summarize": fact_dropping_summarize})
    assert extract_planted_facts([HumanMessage(content=dropped["summary_text"])]) == []

    # 作答与判卷注入假 LLM：闭卷答对 → recall=2；答错 → recall=0
    class EchoLLM:
        def __init__(self, reply):
            self._reply = reply

        def invoke(self, _prompt):
            class R:
                content = self._reply
            return R()

    probe = make_synthetic_probes(facts)["probes"][0]
    good = grade_probe(
        probe_question=probe["question"], probe_type="recall",
        reference_answer=probe["reference_answer"],
        continuation_text=probe["reference_answer"], llm=EchoLLM('{"recall": 2, "accuracy": 5, "artifact_trail": 5, "context_awareness": 5, "continuity": 5, "completeness": 5}'))
    assert good["recall"] == 2
    answered = answer_probe(result["compressed_messages"], probe["question"],
                            llm=EchoLLM("whatever"))
    assert answered == "whatever"
