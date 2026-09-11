"""压缩评测 rubric 解析测试（不调 LLM）：recall 三档 + 五维诊断。"""

import pytest

from evals.compression.rubric import (
    DIMENSIONS,
    JUDGE_PROMPT_VERSION,
    build_judge_prompt,
    parse_judge_response,
)


def test_build_judge_prompt_contains_dimensions_and_recall_scale():
    prompt = build_judge_prompt(
        probe_question="根因是什么？",
        probe_type="recall",
        reference_answer="max_size=0",
        continuation_text="连接池 max_size 为 0",
    )
    for dim in DIMENSIONS:
        assert dim in prompt
    assert '"recall"' in prompt
    assert "REFERENCE ANSWER" in prompt


def test_judge_prompt_premise_is_arm_neutral():
    """判卷前提不得声称「仅基于压缩后上下文」：三组对比下对不压缩/检索组是假前提。"""
    prompt = build_judge_prompt(
        probe_question="q", probe_type="recall",
        reference_answer="a", continuation_text="ans",
    )
    assert "压缩后的会话上下文" not in prompt
    assert "可用的会话上下文" in prompt
    # 版本号随判分口径变更递增，manifest 可追溯
    assert JUDGE_PROMPT_VERSION == "recall-2-1-0+v5dims/v2"


def _valid_response(recall=2, score=5):
    return (
        "```json\n"
        + '{"recall": %d, "accuracy": %d, "artifact_trail": %d, '
          '"context_awareness": %d, "continuity": %d, "completeness": %d, "notes": "ok"}'
          % (recall, score, score, score, score, score)
        + "\n```"
    )


def test_parse_judge_response_recall_and_dims():
    parsed = parse_judge_response(_valid_response(recall=2))
    assert parsed["recall"] == 2
    assert parsed["scores"]["accuracy"] == 5
    assert parsed["overall_probe_score"] == pytest.approx(5.0)


def test_parse_judge_response_partial():
    parsed = parse_judge_response(_valid_response(recall=1, score=3))
    assert parsed["recall"] == 1


def test_parse_judge_response_rejects_bad_recall():
    with pytest.raises(ValueError, match="recall"):
        parse_judge_response(_valid_response(recall=3))
    with pytest.raises(ValueError, match="missing recall"):
        parse_judge_response('{"accuracy": 3}')
    with pytest.raises(ValueError, match="missing dimension"):
        parse_judge_response('{"recall": 2}')
