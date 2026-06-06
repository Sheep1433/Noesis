"""Runner smoke：mock LLM + RAG，跑 1 条 case。"""

import json
from typing import Any, Type
from unittest.mock import MagicMock

import pytest

from evals.report import compute_item_passed
from evals.runners.base import DEFAULT_DATASET, load_dataset
from evals.runners.test_case import run_test_case_item
from evals.scorers.coverage import score_coverage
from evals.scorers.l0_structure import score_l0
from evals.scorers.rag_hit import score_rag
from schemas.case_generate_vo import SceneTestCasesOutput, ScenesTestPointsOutput

SCENES_JSON = [
    {
        "scene_name": "用户登录",
        "scene_description": "账号密码与验证码登录",
        "risk_level": "high",
        "test_points": [
            {
                "point_name": "用户名密码错误提示",
                "point_level": "P0",
                "point_type": "functional",
            },
            {
                "point_name": "验证码过期刷新",
                "point_level": "P1",
                "point_type": "functional",
            },
        ],
    },
    {
        "scene_name": "安全策略",
        "scene_description": "防暴力破解",
        "risk_level": "medium",
        "test_points": [
            {
                "point_name": "连续失败锁定账号",
                "point_level": "P1",
                "point_type": "security",
            },
        ],
    },
]

CASE_JSON = {
    "point_name": "用户名密码错误提示",
    "point_level": "P0",
    "point_type": "functional",
    "preconditions": ["打开登录页"],
    "test_steps": ["输入错误密码", "点击登录"],
    "expected_results": ["提示用户名或密码错误"],
}


class _FakeStructuredLLM:
    def __init__(self, schema: Type[Any], parent: "_FakeLLM", *, include_raw: bool = False):
        self.schema = schema
        self.parent = parent
        self.include_raw = include_raw

    def invoke(self, prompt: str, config=None):
        parsed = self.parent._structured_response(self.schema, prompt)
        if self.include_raw:
            return {"parsed": parsed, "raw": None}
        return parsed

    async def ainvoke(self, prompt: str, config=None):
        return self.invoke(prompt, config=config)


class _FakeLLM:
    def with_structured_output(self, schema: Type[Any], include_raw: bool = False):
        return _FakeStructuredLLM(schema, self, include_raw=include_raw)

    def _structured_response(self, schema: Type[Any], prompt: str):
        if schema is ScenesTestPointsOutput:
            return ScenesTestPointsOutput.model_validate({"scenes": SCENES_JSON})
        if schema is SceneTestCasesOutput:
            return SceneTestCasesOutput.model_validate({"cases": [CASE_JSON]})
        raise AssertionError(f"unexpected schema: {schema}")

    def invoke(self, prompt: str):
        return MagicMock(content=json.dumps(SCENES_JSON, ensure_ascii=False))

    async def ainvoke(self, prompt: str):
        return self.invoke(prompt)


@pytest.fixture
def mock_case_deps(monkeypatch):
    monkeypatch.setattr(
        "agent.case_generate.case_graph.get_llm",
        lambda: _FakeLLM(),
    )

    async def _mock_scene_rag(scene, *, adopted_point_names=None, source_file_names=None):
        return "mock-rag-context", {
            "scene_name": scene.get("scene_name"),
            "channels": {
                "current_requirement": {"hit_ids": ["c1"]},
                "historical_test_cases": {"hit_ids": ["t1"]},
            },
        }

    monkeypatch.setattr(
        "agent.case_generate.case_graph.build_scene_rag_context",
        _mock_scene_rag,
    )


def test_run_test_case_item_smoke(mock_case_deps):
    items = load_dataset(DEFAULT_DATASET)
    item = next(i for i in items if i["id"] == "tc_login_001")
    out = run_test_case_item(
        item,
        dataset_dir=DEFAULT_DATASET.parent,
        eval_run_id="test-run-smoke",
        scope="full",
    )
    assert out["state"]["error"] is None
    assert len(out["state"]["scenes_testpoints"]) >= 2
    assert len(out["state"]["test_cases"]) >= 1

    gt = item["ground_truth"]

    def _mock_cov_judge(golden, scenes):
        return [
            {
                "scene_name": g.get("scene_name"),
                "point_name": g.get("point_name"),
                "covered": any(
                    str(tp.get("point_name") or "") == g.get("point_name")
                    for sc in scenes
                    for tp in sc.get("test_points") or []
                ),
                "matched_point_names": [g.get("point_name")],
            }
            for g in golden
        ]

    scores = {
        "l0": score_l0(out),
        "coverage": score_coverage(out, gt, judge_fn=_mock_cov_judge),
        "rag": score_rag(out, gt),
    }
    scores["passed"] = compute_item_passed(scores, gt)

    assert scores["l0"]["passed"] is True
    assert scores["coverage"]["point_coverage_recall"] == 1.0
    assert scores["passed"] is True
