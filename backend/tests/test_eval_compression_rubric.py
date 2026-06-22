"""压缩评测 rubric 解析测试（不调 LLM）。"""

import pytest

from evals.compression.rubric import DIMENSIONS, build_judge_prompt, parse_judge_response


def test_build_judge_prompt_contains_dimensions():
    prompt = build_judge_prompt(
        probe_question="根因是什么？",
        probe_type="recall",
        reference_answer="max_size=0",
        continuation_text="连接池 max_size 为 0",
    )
    for dim in DIMENSIONS:
        assert dim in prompt


def test_parse_judge_response_json_block():
    raw = """```json
{
  "accuracy": 5,
  "artifact_trail": 4,
  "context_awareness": 4,
  "continuity": 3,
  "completeness": 5,
  "notes": "ok"
}
```"""
    parsed = parse_judge_response(raw)
    assert parsed["scores"]["accuracy"] == 5
    assert parsed["overall_probe_score"] == pytest.approx(4.2, abs=0.01)


def test_parse_judge_response_rejects_missing_dim():
    with pytest.raises(ValueError):
        parse_judge_response('{"accuracy": 3}')
