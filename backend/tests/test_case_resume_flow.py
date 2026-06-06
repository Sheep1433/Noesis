"""测试用例 resume 流程：勾选测试点后应进入阶段 B 生成用例。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.types import Command

from agent.case_generate.case_coordinator import CaseCoordinator
from agent.case_generate.case_graph import (
    TestCaseState,
    build_test_case_graph,
    generate_scenes_testpoints_node,
    generate_test_cases_node,
)


def _merge_command(state: dict, command) -> dict:
    out = dict(state)
    out.update(getattr(command, "update", None) or {})
    return out


@pytest.mark.asyncio
async def test_generate_test_cases_reads_selected_point_names_from_command_update():
    """resume 经 Command 写入 selected_point_names 后，阶段 B 应能匹配到测试点。"""
    state: TestCaseState = {
        "query": "",
        "document_context": "",
        "source_file_names": [],
        "scenes_testpoints": [
            {
                "scene_name": "上传",
                "scene_description": "文件上传",
                "risk_level": "high",
                "test_points": [
                    {"point_name": "上传成功", "point_level": "P0", "point_type": "functional"},
                ],
            }
        ],
        "selected_point_names": ["上传成功"],
        "test_cases": [],
        "retrieval_trace": None,
        "current_phase": "test_cases",
        "error": None,
    }

    mock_case = {
        "case_id": "TC-001",
        "point_name": "上传成功",
        "point_level": "P0",
        "point_type": "functional",
        "scene_name": "上传",
        "preconditions": [],
        "test_steps": ["步骤1"],
        "expected_results": ["预期1"],
    }

    with patch(
        "agent.case_generate.case_graph._generate_cases_streaming",
        new=AsyncMock(return_value=([mock_case], {"上传": {"scene_name": "上传", "channels": {}}})),
    ):
        cmd = await generate_test_cases_node(state)

    merged = _merge_command(state, cmd)
    assert merged["current_phase"] == "finish"
    assert len(merged["test_cases"]) == 1
    assert merged["test_cases"][0]["test_steps"] == ["步骤1"]


@pytest.mark.asyncio
async def test_generate_test_cases_empty_selection_returns_error_not_silent_finish():
    state: TestCaseState = {
        "query": "",
        "document_context": "",
        "source_file_names": [],
        "scenes_testpoints": [{"scene_name": "s", "test_points": []}],
        "selected_point_names": [],
        "test_cases": [],
        "retrieval_trace": None,
        "current_phase": "test_cases",
        "error": None,
    }
    cmd = await generate_test_cases_node(state)
    merged = _merge_command(state, cmd)
    assert merged.get("error")
    assert merged["test_cases"] == []


@pytest.mark.asyncio
async def test_graph_interrupt_before_cases_and_resume_with_selected_names():
    """compile interrupt_before 后 resume 应带着 selected_point_names 进入 generate_test_cases。"""
    from langgraph.checkpoint.memory import MemorySaver

    scenes = [
        {
            "scene_name": "登录",
            "scene_description": "用户登录",
            "risk_level": "medium",
            "test_points": [
                {"point_name": "密码错误", "point_level": "P0", "point_type": "functional"},
            ],
        }
    ]

    def _mock_scenes_node(_state):
        return Command(
            update={
                "scenes_testpoints": scenes,
                "selected_point_names": [],
                "current_phase": "testpoints_confirm",
            }
        )

    with patch(
        "agent.case_generate.case_graph.generate_scenes_testpoints_node",
        side_effect=_mock_scenes_node,
    ):
        graph = build_test_case_graph()
        app = graph.compile(
            checkpointer=MemorySaver(),
            interrupt_before=["generate_test_cases"],
        )

    config = {"configurable": {"thread_id": "resume-flow-test"}}

    chunks = []
    async for raw in app.astream(
        {
            "query": "q",
            "document_context": "doc",
            "source_file_names": [],
            "scenes_testpoints": [],
            "selected_point_names": [],
            "test_cases": [],
            "retrieval_trace": None,
            "current_phase": "scenes_testpoints",
            "error": None,
        },
        config,
    ):
        chunks.append(raw)

    assert any(
        isinstance(v, dict) and v.get("current_phase") == "testpoints_confirm"
        for chunk in chunks
        for v in (chunk.values() if isinstance(chunk, dict) else [])
    )

    mock_case = {
        "case_id": "TC-001",
        "point_name": "密码错误",
        "point_level": "P0",
        "point_type": "functional",
        "scene_name": "登录",
        "preconditions": [],
        "test_steps": ["输入错误密码"],
        "expected_results": ["提示错误"],
    }

    with patch(
        "agent.case_generate.case_graph._generate_cases_streaming",
        new=AsyncMock(return_value=([mock_case], {"登录": {}})),
    ):
        resume_chunks = []
        async for mode, raw in app.astream(
            Command(
                update={
                    "selected_point_names": ["密码错误"],
                    "current_phase": "test_cases",
                }
            ),
            config,
            stream_mode=["updates", "custom"],
        ):
            if mode == "updates":
                resume_chunks.append(raw)

    finish_updates = [
        v
        for chunk in resume_chunks
        if isinstance(chunk, dict)
        for v in chunk.values()
        if isinstance(v, dict) and v.get("current_phase") == "finish"
    ]
    assert finish_updates
    assert finish_updates[0]["test_cases"][0]["test_steps"] == ["输入错误密码"]


@pytest.mark.asyncio
async def test_resume_agent_emits_scene_cases_via_graph_custom_stream():
    coordinator = CaseCoordinator()
    thread_id = "tc-resume-emit"

    mock_case = {
        "case_id": "TC-001",
        "point_name": "上传成功",
        "point_level": "P0",
        "point_type": "functional",
        "scene_name": "上传",
        "preconditions": [],
        "test_steps": ["步骤1"],
        "expected_results": ["预期1"],
    }

    async def _fake_astream(_command, _config, stream_mode=None):
        yield "custom", {
            "kind": "scene-cases",
            "payload": {"scene_name": "上传", "cases": [mock_case]},
        }
        yield "updates", {"generate_test_cases": {"current_phase": "finish", "test_cases": [mock_case]}}

    mock_app = MagicMock()
    mock_app.astream = _fake_astream

    coordinator._graph_instances[thread_id] = {
        "app": mock_app,
        "config_base": {"configurable": {"thread_id": "x"}},
        "qa_type": "TEST_CASE_QA",
        "current_phase": "testpoints_confirm",
        "scenes_testpoints": [
            {
                "scene_name": "上传",
                "test_points": [{"point_name": "上传成功", "point_level": "P0"}],
            }
        ],
        "query": "q",
    }

    events = []
    async for ev in coordinator.resume_agent(thread_id, selected_point_names=["上传成功"]):
        events.append(ev)

    scene_cases = [e for e in events if e.get("type") == "scene-cases"]
    assert scene_cases
    assert scene_cases[0].get("sceneName") == "上传"
    assert scene_cases[0].get("cases", [{}])[0].get("test_steps") == ["步骤1"]
    assert not [e for e in events if e.get("type") == "text-delta"]
    finish = [e for e in events if e.get("type") == "finish"]
    assert finish and finish[0].get("total") == 1
