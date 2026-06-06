"""TestCaseOutput 列表字段 coercion 单测。"""

from schemas.case_generate_vo import TestCaseOutput


def test_expected_results_accepts_json_string():
    raw = '\n["验证码图片正常展示", "刷新后验证码变更"]\n'
    out = TestCaseOutput(
        point_name="验证码展示",
        point_level="P1",
        point_type="functional",
        preconditions=[],
        test_steps=["打开登录页"],
        expected_results=raw,
    )
    assert out.expected_results == ["验证码图片正常展示", "刷新后验证码变更"]


def test_list_fields_accept_native_list():
    out = TestCaseOutput(
        point_name="登录",
        point_level="P0",
        point_type="functional",
        preconditions=["已注册"],
        test_steps=["输入账号", "点击登录"],
        expected_results=["跳转首页"],
    )
    assert out.test_steps == ["输入账号", "点击登录"]
