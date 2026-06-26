"""promptfoo 配置与断言（无 LLM / 无 promptfoo CLI）。"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from evals.case.shared.assertions import assert_l0, assert_scene_name_recall

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
    assert first["vars"]["scenario_description"]
    assert first["vars"]["document_path"].startswith("documents/")


def test_testpoints_uses_llm_rubric_for_coverage():
    asserts = (_load_config("testpoints").get("defaultTest") or {}).get("assert") or []
    types = [a.get("type") for a in asserts]
    assert "llm-rubric" in types
    coverage = [a for a in asserts if a.get("metric") == "point_coverage_recall"][0]
    assert coverage["type"] == "llm-rubric"


def test_testpoints_includes_metrics():
    asserts = (_load_config("testpoints").get("defaultTest") or {}).get("assert") or []
    assert {a.get("metric") for a in asserts} == {
        "l0",
        "point_coverage_recall",
        "scene_name_recall",
    }


def test_testpoints_case_ids_unique():
    tests = _load_config("testpoints").get("tests") or []
    ids = [t["vars"]["item_id"] for t in tests]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 8


def test_rag_config_metrics():
    asserts = (_load_config("rag").get("defaultTest") or {}).get("assert") or []
    metrics = {a.get("metric") for a in asserts}
    assert "historical_requirements_recall_at_3" in metrics
    assert "document_context_present" in metrics


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
    result = assert_l0(output, {"vars": {"ground_truth": {}}})
    assert result["pass"] is True


def test_assert_scene_name_recall():
    output = json.dumps(
        {
            "state": {
                "scenes_testpoints": [
                    {"scene_name": "用户登录", "test_points": []},
                ],
            }
        },
        ensure_ascii=False,
    )
    result = assert_scene_name_recall(
        output,
        {
            "vars": {
                "ground_truth": {
                    "golden_test_points": [
                        {"scene_name": "用户登录", "point_name": "x"},
                        {"scene_name": "会话管理", "point_name": "y"},
                    ],
                },
            },
        },
    )
    assert result["score"] == 0.5
