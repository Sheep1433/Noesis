"""E2E 失败归因规则测试（纯函数，无外部依赖）。"""

from evals.agent.rag.attribution import (
    attribute_failures,
    attribute_record,
    render_attribution_md,
)


def _record(**overrides):
    base = {
        "sample_id": "s1",
        "question_id": "q1",
        "judge": {"verdict": "rejected"},
        "kb_tool_called": True,
        "tool_stats": {"search_knowledge_base": 2},
        "retrieval_hit": True,
    }
    base.update(overrides)
    return base


def test_non_rejected_not_attributed():
    assert attribute_record(_record(judge={"verdict": "accepted"}))["category"] is None


def test_retrieval_miss():
    out = attribute_record(_record(retrieval_hit=False))
    assert out["category"] == "retrieval_miss"


def test_tool_anomaly_when_kb_not_called():
    out = attribute_record(_record(kb_tool_called=False))
    assert out["category"] == "tool_anomaly"
    assert "未调用" in out["reason"]


def test_tool_anomaly_notes_web_search_substitution():
    out = attribute_record(
        _record(kb_tool_called=False, tool_stats={"web_search": 3}))
    assert out["category"] == "tool_anomaly"
    assert "web_search" in out["reason"]


def test_reasoning_error_when_hit_and_tools_normal():
    out = attribute_record(_record())
    assert out["category"] == "reasoning_error"


def test_manual_review_when_no_retrieval_data():
    out = attribute_record(_record(retrieval_hit=None))
    assert out["category"] == "manual_review"


def test_attribute_failures_counts_and_md():
    records = [
        _record(sample_id="a", question_id="q1", retrieval_hit=False),
        _record(sample_id="b", question_id="q2"),
        _record(sample_id="c", question_id="q3", judge={"verdict": "accepted"}),
    ]
    result = attribute_failures(records)
    assert result["counts"]["retrieval_miss"] == 1
    assert result["counts"]["reasoning_error"] == 1
    assert len(result["details"]) == 2
    md = render_attribution_md(result)
    assert "检索没召回" in md and "推理错" in md and "q2" in md
