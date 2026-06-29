"""promptfoo 配置与断言（无 LLM / 无 promptfoo CLI）。"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from evals.case.report import format_case_summary_lines, summarize_promptfoo_eval
from evals.case.shared.assertions import assert_l0

CASE_ROOT = Path(__file__).resolve().parents[1] / "evals" / "case"


def _config_path(phase: str) -> Path:
    return CASE_ROOT / phase / "promptfooconfig.yaml"


def _load_config(phase: str) -> dict:
    return yaml.safe_load(_config_path(phase).read_text(encoding="utf-8"))


def test_testpoints_config_has_cases():
    cfg = _load_config("testpoints")
    tests = cfg.get("tests") or []
    assert len(tests) >= 1
    first = tests[0]
    assert first.get("description")
    assert first["vars"]["item_id"]
    assert first["vars"]["document_path"].startswith("documents/")
    assert "scenario_description" not in first["vars"]


def test_testpoints_vars_minimal():
    first = (_load_config("testpoints").get("tests") or [])[0]
    vars_ = first["vars"]
    assert set(vars_.keys()) == {"item_id", "document_path", "golden_test_points_json"}


def test_case_cli_phase_aliases():
    from evals.case.__main__ import PHASE_ALIASES, _resolve_phase

    assert _resolve_phase("testpoints") == "testpoints"
    assert _resolve_phase("stage-a") == "testpoints"
    assert _resolve_phase("rag") == "rag"
    assert _resolve_phase("stage-b") == "rag"
    assert PHASE_ALIASES["stage-a"] == "testpoints"


def test_testpoints_prompts_fixed_string():
    prompts = _load_config("testpoints").get("prompts") or []
    assert prompts == ["请根据需求文档生成测试场景与测试点"]
    assert "{{" not in prompts[0]


def test_golden_yaml_counts():
    from evals.case.testpoints.golden_loader import load_all_golden

    golden = load_all_golden()
    assert len(golden) == 20
    for item_id, points in golden.items():
        scenes = {p["scene_name"] for p in points}
        assert 3 <= len(scenes) <= 6, f"{item_id}: {len(scenes)} scenes"
        assert 20 <= len(points) <= 50, f"{item_id}: {len(points)} points"


def test_testpoints_uses_llm_rubric_for_coverage():
    asserts = (_load_config("testpoints").get("defaultTest") or {}).get("assert") or []
    types = [a.get("type") for a in asserts]
    assert types.count("llm-rubric") == 2
    recall = [a for a in asserts if a.get("metric") == "point_coverage_recall"][0]
    precision = [a for a in asserts if a.get("metric") == "point_coverage_precision"][0]
    assert recall["type"] == "llm-rubric"
    assert precision["type"] == "llm-rubric"


def test_testpoints_includes_metrics():
    asserts = (_load_config("testpoints").get("defaultTest") or {}).get("assert") or []
    assert {a.get("metric") for a in asserts} == {
        "l0",
        "point_coverage_recall",
        "point_coverage_precision",
    }


def test_testpoints_case_ids_unique():
    tests = _load_config("testpoints").get("tests") or []
    ids = [t["vars"]["item_id"] for t in tests]
    assert len(ids) == len(set(ids))
    assert len(ids) == 20


def test_rag_config_metrics():
    asserts = (_load_config("rag").get("defaultTest") or {}).get("assert") or []
    metrics = {a.get("metric") for a in asserts}
    assert "historical_requirements_recall_at_3" in metrics
    assert "document_context_present" in metrics


def test_rag_config_item_id():
    tests = _load_config("rag").get("tests") or []
    assert tests
    assert tests[0]["vars"]["item_id"] == "prd_001_rag"


def test_rag_requires_rag_scene():
    tests = _load_config("rag").get("tests") or []
    assert tests
    assert "rag_scene" in tests[0]["vars"]


def test_assert_l0_on_valid_output():
    output = json.dumps(
        {
            "state": {
                "error": None,
                "scenes_testpoints": [
                    {
                        "scene_name": "用户登录",
                        "test_points": [
                            {
                                "point_name": "用户名密码错误提示",
                                "point_level": "P0",
                                "point_type": "functional",
                            }
                        ],
                    }
                ],
            }
        },
        ensure_ascii=False,
    )
    result = assert_l0(output, {"vars": {}})
    assert result["pass"] is True


def test_summarize_promptfoo_eval_metrics():
    data = {
        "evalId": "eval-test",
        "results": {
            "results": [
                {
                    "success": True,
                    "namedScores": {
                        "l0": 1.0,
                        "point_coverage_recall": 1.0,
                        "point_coverage_precision": 0.8,
                    },
                    "testCase": {"description": "prd_001", "vars": {"item_id": "prd_001"}},
                },
                {
                    "success": False,
                    "namedScores": {
                        "l0": 1.0,
                        "point_coverage_recall": 0.5,
                        "point_coverage_precision": 0.6,
                    },
                    "testCase": {"description": "prd_002", "vars": {"item_id": "prd_002"}},
                },
            ]
        },
    }
    summary = summarize_promptfoo_eval(data, tag="baseline", phase="testpoints")
    assert summary["dataset_size"] == 2
    assert summary["pass_count"] == 1
    assert summary["metrics"]["point_coverage_recall"]["mean"] == 0.75
    assert summary["metrics"]["l0"]["pass_count"] == 2
    assert summary["worst_recall"][0]["item_id"] == "prd_002"

    lines = "\n".join(format_case_summary_lines(summary))
    assert "point_coverage_recall_mean" in lines
    assert "Lowest recall: prd_002 (0.50)" in lines
