"""coverage_scorer 单元测试（无 LLM）。"""

from __future__ import annotations

from evals.case.shared.coverage_scorer import (
    MatchLevel,
    match_level,
    score_point_coverage_precision,
    score_point_coverage_recall,
)


def _scenes(*point_names: str) -> list[dict]:
    return [
        {
            "scene_name": "场景",
            "test_points": [
                {"point_name": n, "point_level": "P1", "point_type": "functional"}
                for n in point_names
            ],
        }
    ]


def test_match_level_certain_on_shared_terms():
    level = match_level("用户名或密码错误统一提示文案", "用户名或密码错误提示文案一致")
    assert level in (MatchLevel.CERTAIN, MatchLevel.BORDERLINE)


def test_match_level_none_on_unrelated():
    assert match_level("支付超时关单", "登录页验证码刷新") == MatchLevel.NONE


def test_recall_covers_golden_point():
    golden = [{"scene_name": "异常", "point_name": "连续失败5次锁定15分钟"}]
    scenes = _scenes("同一用户名连续5次失败后锁定15分钟")
    result = score_point_coverage_recall(scenes, golden, use_llm_borderline=False)
    assert result["score"] >= 0.5


def test_recall_empty_golden():
    result = score_point_coverage_recall(_scenes("任意"), [], use_llm_borderline=False)
    assert result["score"] == 0.0


def test_precision_doc_supported_without_golden_match():
    golden = [{"scene_name": "x", "point_name": "完全不相关的金标准"}]
    doc = "登录接口 P99 须小于 800ms，传输强制 HTTPS。"
    scenes = _scenes("登录接口P99小于800ms")
    result = score_point_coverage_precision(
        scenes, golden, doc, use_llm_borderline=False
    )
    assert result["score"] == 1.0


def test_assertions_integration_style():
    from evals.case.shared.assertions import assert_point_coverage_recall
    import json

    output = json.dumps(
        {
            "state": {
                "scenes_testpoints": _scenes("记住我默认不勾选", "传输强制HTTPS"),
            }
        },
        ensure_ascii=False,
    )
    golden_json = [
        {"scene_name": "登录页", "point_name": "记住我默认不勾选"},
        {"scene_name": "安全", "point_name": "传输强制HTTPS"},
    ]
    ctx = {
        "vars": {
            "document_path": "documents/prd_001.md",
            "golden_test_points_json": json.dumps(golden_json, ensure_ascii=False),
        }
    }
    result = assert_point_coverage_recall(output, ctx)
    assert result["pass"] is True
    assert float(result["score"]) == 1.0
