"""L0 / coverage / rag 评分器测试（无真实 LLM）。"""

from evals.case.scoring import score_coverage, score_l0, score_rag


def _sample_state():
    return {
        "scenes_testpoints": [
            {
                "scene_name": "用户登录",
                "scene_description": "账号密码登录",
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
        ],
        "test_cases": [
            {
                "case_id": "TC-001",
                "point_name": "用户名密码错误提示",
                "point_level": "P0",
                "point_type": "functional",
                "scene_name": "用户登录",
                "preconditions": ["已打开登录页"],
                "test_steps": ["输入错误密码", "点击登录"],
                "expected_results": ["提示用户名或密码错误"],
            },
        ],
        "error": None,
        "current_phase": "finish",
    }


def test_l0_passes_valid_output():
    l0 = score_l0({"state": _sample_state()})
    assert l0["schema_ok"] is True
    assert l0["passed"] is True


def test_coverage_recall_with_mock_judge():
    def _mock_judge(golden, scenes):
        return [
            {
                "scene_name": g.get("scene_name"),
                "point_name": g.get("point_name"),
                "covered": True,
                "matched_point_names": [g.get("point_name")],
            }
            for g in golden
        ]

    gt = {
        "golden_test_points": [
            {"scene_name": "用户登录", "point_name": "用户名密码错误提示"},
            {"scene_name": "用户登录", "point_name": "验证码过期刷新"},
        ],
    }
    result = score_coverage({"state": _sample_state()}, gt, judge_fn=_mock_judge)
    assert result["point_coverage_recall"] == 1.0
