"""阶段 B 按场景批量生成用例单测。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from agent.case_generate.case_graph import _generate_scene_cases, _normalize_scene_cases
from schemas.case_generate_vo import SceneTestCasesOutput, TestCaseOutput


def test_normalize_scene_cases_assigns_ids_and_meta():
    points = [
        {"point_name": "A", "point_level": "P0", "point_type": "functional"},
        {"point_name": "B", "point_level": "P1", "point_type": "security"},
    ]
    raw = [
        TestCaseOutput(
            point_name="A",
            point_level="P0",
            point_type="functional",
            preconditions=[],
            test_steps=["s1"],
            expected_results=["e1"],
        ),
        TestCaseOutput(
            point_name="B",
            point_level="P1",
            point_type="security",
            preconditions=[],
            test_steps=["s2"],
            expected_results=["e2"],
        ),
    ]
    cases, err = _normalize_scene_cases("登录", points, raw, case_id_start=0)
    assert err is None
    assert len(cases) == 2
    assert cases[0]["case_id"] == "TC-001"
    assert cases[1]["case_id"] == "TC-002"
    assert cases[0]["scene_name"] == "登录"


@pytest.mark.asyncio
async def test_generate_scene_cases_single_llm_invoke():
    points = [{"point_name": "密码错误", "point_level": "P0", "point_type": "functional"}]
    mock_result = SceneTestCasesOutput(
        cases=[
            TestCaseOutput(
                point_name="密码错误",
                point_level="P0",
                point_type="functional",
                preconditions=[],
                test_steps=["输入错误密码"],
                expected_results=["提示错误"],
            )
        ]
    )
    mock_llm = MagicMock()
    mock_structured = AsyncMock()
    mock_structured.ainvoke.return_value = {"parsed": mock_result, "raw": MagicMock()}
    mock_llm.with_structured_output.return_value = mock_structured

    with patch("agent.case_generate.case_graph.get_llm", return_value=mock_llm):
        cases, err = await _generate_scene_cases("登录", points, "ctx", 0)

    assert err is None
    assert len(cases) == 1
    mock_llm.with_structured_output.assert_called_once_with(
        SceneTestCasesOutput, include_raw=True
    )
    mock_structured.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_scene_cases_repairs_stringified_malformed_cases():
    """LLM 将 cases 序列化为含 `]}]` 尾部的非法 JSON 时，应从 raw tool call 兜底解析。"""
    points = [
        {
            "point_name": "无障碍",
            "point_level": "P1",
            "point_type": "functional",
        }
    ]
    bad_cases = '\n[{"point_name": "无障碍", "point_level": "P1", "point_type": "functional", "preconditions": [], "test_steps": ["检查页面"], "expected_results": ["符合 WCAG 2.1 无障碍标准"]}]\n'
    # 模型典型错误：对象结束前多一个 `]`
    bad_cases = bad_cases.replace('"}]', '"]}]')

    raw_msg = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "SceneTestCasesOutput",
                "args": {"cases": bad_cases},
                "id": "tc-1",
                "type": "tool_call",
            }
        ],
    )
    mock_llm = MagicMock()
    mock_structured = AsyncMock()
    mock_structured.ainvoke.return_value = {
        "parsed": None,
        "raw": raw_msg,
        "parsing_error": None,
    }
    mock_llm.with_structured_output.return_value = mock_structured

    with patch("agent.case_generate.case_graph.get_llm", return_value=mock_llm):
        cases, err = await _generate_scene_cases("无障碍", points, "ctx", 0)

    assert err is None
    assert len(cases) == 1
    assert cases[0]["point_name"] == "无障碍"
