"""coverage scorer（mock LLM Judge，无 DashScope）。"""

from evals.case.scoring import score_coverage


def _mock_judge_all_covered(golden, scenes):
    return [
        {
            "scene_name": g.get("scene_name"),
            "point_name": g.get("point_name"),
            "covered": True,
            "matched_point_names": [g.get("point_name")],
        }
        for g in golden
    ]


def _mock_judge_partial(golden, scenes):
    out = _mock_judge_all_covered(golden, scenes)
    if out:
        out[0]["covered"] = False
        out[0]["matched_point_names"] = []
    return out


def _run_output_with_scenes():
    return {
        "state": {
            "scenes_testpoints": [
                {
                    "scene_name": "用户登录",
                    "test_points": [
                        {"point_name": "用户名密码错误提示", "point_level": "P0", "point_type": "functional"},
                        {"point_name": "验证码过期刷新", "point_level": "P1", "point_type": "functional"},
                    ],
                }
            ],
        }
    }


def test_coverage_recall_full():
    gt = {
        "golden_test_points": [
            {"scene_name": "用户登录", "point_name": "用户名密码错误提示"},
            {"scene_name": "用户登录", "point_name": "验证码过期刷新"},
        ],
    }
    result = score_coverage(_run_output_with_scenes(), gt, judge_fn=_mock_judge_all_covered)
    assert result["point_coverage_recall"] == 1.0
    assert result["covered_count"] == 2


def test_coverage_recall_partial():
    gt = {
        "golden_test_points": [
            {"scene_name": "用户登录", "point_name": "用户名密码错误提示"},
            {"scene_name": "用户登录", "point_name": "验证码过期刷新"},
        ],
    }
    result = score_coverage(_run_output_with_scenes(), gt, judge_fn=_mock_judge_partial)
    assert result["point_coverage_recall"] == 0.5


def test_coverage_skipped_without_golden():
    result = score_coverage(_run_output_with_scenes(), {})
    assert result["skipped"] is True
