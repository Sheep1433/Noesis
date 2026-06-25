"""promptfoo 配置与断言（无 LLM / 无 promptfoo CLI）。"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from evals.case.scoring import assert_l0

PROMPTFOO_DIR = Path(__file__).resolve().parents[1] / "evals" / "case" / "promptfoo"


def _load_config() -> dict:
    return yaml.safe_load((PROMPTFOO_DIR / "promptfooconfig.yaml").read_text(encoding="utf-8"))


def test_promptfooconfig_has_cases():
    cfg = _load_config()
    tests = cfg.get("tests") or []
    assert len(tests) >= 1
    first = tests[0]
    assert first.get("description")
    assert first["vars"]["item_id"]
    assert first["vars"]["scenario_description"]
    assert first["vars"]["document_path"].startswith("fixtures/documents/")
    assert "item" not in first["vars"]


def test_promptfooconfig_uses_llm_rubric_for_coverage():
    asserts = (_load_config().get("defaultTest") or {}).get("assert") or []
    types = [a.get("type") for a in asserts]
    assert "llm-rubric" in types
    coverage = [a for a in asserts if a.get("metric") == "point_coverage_recall"][0]
    assert coverage["value"].endswith("coverage_rubric.txt")
    assert coverage["provider"]["id"].endswith("judge_provider.py")


def test_promptfooconfig_includes_all_metrics():
    asserts = (_load_config().get("defaultTest") or {}).get("assert") or []
    assert {a.get("metric") for a in asserts} == {"l0", "point_coverage_recall", "rag_hit_at_3"}


def test_promptfooconfig_case_ids_unique():
    tests = _load_config().get("tests") or []
    ids = [t["vars"]["item_id"] for t in tests]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 8


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
                "test_cases": [],
            }
        },
        ensure_ascii=False,
    )
    result = assert_l0(
        output,
        {
            "vars": {
                "ground_truth": {
                    "golden_test_points": [
                        {"scene_name": "用户登录", "point_name": "用户名密码错误提示"},
                    ],
                },
            },
        },
    )
    assert result["pass"] is True
