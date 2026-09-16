"""DeepResearch RACE 判分测试（零 LLM）：解析 / 加权 / 相对分口径。"""

import json

import pytest

from evals.agent.deepresearch.race import (
    DIMS,
    calculate_weighted_scores,
    format_criteria_list,
    parse_judge_output,
    race_record,
)

_CRITERIA = {
    "dimension_weight": {"comprehensiveness": 0.3, "insight": 0.36,
                          "instruction_following": 0.2, "readability": 0.14},
    "criterions": {
        "comprehensiveness": [
            {"criterion": "覆盖 A", "explanation": "x", "weight": 1.0},
            {"criterion": "覆盖 B", "explanation": "y", "weight": 3.0},
        ],
        "insight": [
            {"criterion": "洞察 A", "explanation": "x", "weight": 1.0},
        ],
        "instruction_following": [
            {"criterion": "指令 A", "explanation": "x", "weight": 1.0},
        ],
        "readability": [
            {"criterion": "可读 A", "explanation": "x", "weight": 1.0},
        ],
    },
}


def test_parse_judge_output_tolerates_markdown_fence_and_validates_dims():
    body = {"comprehensiveness": [{"criterion": "c", "article_1_score": 5,
                                   "article_2_score": 6}],
            "insight": [], "instruction_following": [], "readability": []}
    fenced = f"前置说明\n```json\n{json.dumps(body)}\n```\n后置"
    assert parse_judge_output(fenced) == body
    with pytest.raises(ValueError, match="缺维度"):
        parse_judge_output('{"comprehensiveness": []}')
    with pytest.raises(ValueError, match="无 JSON"):
        parse_judge_output("完全没有 JSON 的输出")


def test_weighted_scores_criterion_weights_and_average_fallback():
    judge = {
        "comprehensiveness": [
            # 加权均值 = (4*1 + 8*3) / 4 = 7
            {"criterion": "覆盖 A", "article_1_score": 4, "article_2_score": 4},
            {"criterion": "覆盖 B", "article_1_score": 8, "article_2_score": 6},
        ],
        "insight": [
            # 标准文本对不上 → 平均权重 (1+3)/2？不：insight 维度只有 1 条权重 1.0
            {"criterion": "未知标准", "article_1_score": 6, "article_2_score": 6},
        ],
        "instruction_following": [
            {"criterion": "指令 A", "article_1_score": 8, "article_2_score": 8},
        ],
        "readability": [
            {"criterion": "可读 A", "article_1_score": 10, "article_2_score": 10},
        ],
    }
    scores = calculate_weighted_scores(judge, _CRITERIA)
    assert scores["target"]["dims"]["comprehensiveness"] == pytest.approx(7.0)
    # insight 的「未知标准」按该维度平均权重兜底 → 仍计入（官方口径）
    assert scores["target"]["dims"]["insight"] == pytest.approx(6.0)
    expect_total = 7 * 0.3 + 6 * 0.36 + 8 * 0.2 + 10 * 0.14
    assert scores["target"]["total"] == pytest.approx(expect_total)


def test_race_record_relative_score_parity_is_half():
    judge = {
        "comprehensiveness": [{"criterion": "覆盖 A", "article_1_score": 6,
                               "article_2_score": 6}],
        "insight": [{"criterion": "洞察 A", "article_1_score": 6,
                     "article_2_score": 6}],
        "instruction_following": [{"criterion": "指令 A", "article_1_score": 6,
                                   "article_2_score": 6}],
        "readability": [{"criterion": "可读 A", "article_1_score": 6,
                         "article_2_score": 6}],
    }
    rec = race_record(task_prompt="t", article_ours="a",
                      reference={"article": "b"}, criteria=_CRITERIA,
                      judge_output=judge)
    # 两篇同分 → 相对分 0.5（与专家参考持平）
    assert rec["overall_score"] == pytest.approx(0.5)
    for d in DIMS:
        assert rec[d] == pytest.approx(0.5)


def test_format_criteria_list_strips_weights():
    out = json.loads(format_criteria_list(_CRITERIA))
    assert out["comprehensiveness"][0] == {"criterion": "覆盖 A", "explanation": "x"}
    assert "weight" not in out["comprehensiveness"][0]
