"""promptfoo 测试生成与断言（无 LLM / 无 promptfoo CLI）。"""

import json

from evals.case.dataset import DEFAULT_DATASET, load_dataset
from evals.case.promptfoo.tests import generate_tests
from evals.case.scoring import assert_l0


def test_generate_tests_default():
    tests = generate_tests({"scope": "testpoints", "limit": 1})
    assert len(tests) == 1
    assert tests[0]["vars"]["item_id"]
    assert any(a.get("metric") == "l0" for a in tests[0]["assert"])


def test_assert_l0_on_valid_output():
    item = load_dataset(DEFAULT_DATASET)[0]
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
    result = assert_l0(output, {"vars": {"item": item}})
    assert result["pass"] is True
