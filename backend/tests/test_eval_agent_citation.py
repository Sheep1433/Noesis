"""引用溯源评测测试：格式遵循 / 已知失败模式 / 引用正确率 / 事实溯源（fake LLM）。"""

import json

import pytest

from evals.agent.citation import (
    build_fact_grounding_prompt,
    citation_metrics,
    judge_fact_grounding,
    parse_citations,
    parse_fact_grounding_response,
)

GOOD_ANSWER = (
    "默认限制是每文件 10 MiB。"
    "[citation:pr-18421-multipart-file-validation-limits.txt]"
    "(kb:erb-eval/pr-18421-multipart-file-validation-limits.txt)"
)


class FakeLLM:
    def __init__(self, reply):
        self._reply = reply

    def invoke(self, _prompt):
        class R:
            content = self._reply
        return R()


def test_parse_citations_extracts_label_and_ref():
    cites = parse_citations(GOOD_ANSWER)
    assert len(cites) == 1
    assert cites[0]["label"] == "pr-18421-multipart-file-validation-limits.txt"
    assert cites[0]["ref"].startswith("kb:erb-eval/")


def test_format_compliant_and_accuracy_for_good_answer():
    m = citation_metrics(GOOD_ANSWER, expected_doc_files=["pr-18421-multipart-file-validation-limits.txt"])
    assert m["format_compliant"] is True
    assert m["failure_modes"] == []
    assert m["citation_accuracy"] == 1.0


def test_failure_mode_file_protocol_prefix():
    answer = "答案。[citation:x.txt](file:erb-eval/x.txt)"
    m = citation_metrics(answer, expected_doc_files=["x.txt"])
    assert m["format_compliant"] is False
    assert any("伪协议头" in f for f in m["failure_modes"])


def test_failure_mode_percent_encoded_filename():
    answer = "答案。[citation:配置.md](kb:erb-eval/%E9%85%8D%E7%BD%AE.md)"
    m = citation_metrics(answer, expected_doc_files=["配置.md"])
    # 编码后的 ref 解码后能对上 GT（correct 捕捉），但格式判定记失败模式
    assert any("URL 编码" in f for f in m["failure_modes"])
    assert m["format_compliant"] is False


def test_citation_accuracy_zero_when_wrong_doc():
    answer = "[citation:wrong.txt](kb:erb-eval/wrong.txt)"
    m = citation_metrics(answer, expected_doc_files=["right.txt"])
    assert m["citation_accuracy"] == 0.0


def test_citation_accuracy_none_without_kb_citations():
    m = citation_metrics("没有引用", expected_doc_files=["a.txt"])
    assert m["citation_accuracy"] is None
    assert m["format_compliant"] is False


def test_fact_grounding_restricts_evidence_to_cited_files():
    outputs = [{
        "name": "search_knowledge_base",
        "output": json.dumps({
            "results": [
                {"file_name": "cited.md", "excerpt": "limit is 10 MiB"},
                {"file_name": "uncited.md", "excerpt": "totally unrelated"},
            ]
        }, ensure_ascii=False),
    }]
    reply = json.dumps([{"fact_index": 1, "supported": True, "notes": ""}])
    out = judge_fact_grounding(
        answer_facts=["per-file limit is 10 MiB"],
        tool_outputs=outputs,
        cited_files=["cited.md"],
        llm=FakeLLM(reply),
    )
    assert out["grounding_rate"] == 1.0
    assert out["parse_error"] is None


def test_fact_grounding_parse_failure_marks_invalid():
    outputs = [{
        "name": "search_knowledge_base",
        "output": json.dumps({"results": [{"file_name": "cited.md", "excerpt": "x"}]}),
    }]
    out = judge_fact_grounding(
        answer_facts=["fact1", "fact2"],
        tool_outputs=outputs,
        cited_files=["cited.md"],
        llm=FakeLLM("garbage"),
    )
    assert out["grounding_rate"] is None
    assert out["parse_error"]


def test_parse_fact_grounding_response_validates_indices():
    with pytest.raises(ValueError, match="fact_index mismatch"):
        parse_fact_grounding_response('[{"fact_index": 2, "supported": true}]', n_facts=1)
    with pytest.raises(ValueError, match="expect 2"):
        parse_fact_grounding_response('[{"fact_index": 1, "supported": true}]', n_facts=2)


def test_build_fact_grounding_prompt_includes_facts_and_evidence():
    prompt = build_fact_grounding_prompt(
        answer_facts=["f1"], evidence={"doc.md": "excerpt text"})
    assert "f1" in prompt and "doc.md" in prompt and "excerpt text" in prompt
