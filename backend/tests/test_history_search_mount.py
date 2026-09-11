"""会话历史检索工具挂载面测试（session-history-search）。

SuperAgent 默认挂载（与 search_memory 并列）、task-worker 不带（隔离 loop
不可触 pg_manager）、GeneralQA 不挂。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _super_agent_patches(captured_tools, captured_worker_tools):
    def _capture_create_agent(**kwargs):
        captured_tools.extend(kwargs.get("tools") or [])
        return MagicMock()

    def _capture_assert(tools):
        captured_worker_tools.extend(tools)

    return {
        # 重协作者打桩：backend 网络 / skills 解析 / prompt 上下文 / agent 编译
        "ensure_user_memory_files": lambda *a, **k: None,
        "create_agent_backend": AsyncMock(return_value=MagicMock()),
        "build_web_search_tools": lambda backend=None: [],
        "resolve_skill_sources_for_session": lambda *a, **k: [],
        # 同步子 Agent 装配期解析模型 / 构建中间件，测试环境无模型配置，桩掉
        "get_llm": lambda *a, **k: MagicMock(),
        "build_noesis_middleware": lambda **k: [],
        "ContextResolver": SimpleNamespace(
            resolve=staticmethod(lambda *a, **k: SimpleNamespace(
                system_prompt="p", memory_sources=[]
            ))
        ),
        "create_noesis_agent": _capture_create_agent,
        "assert_no_bg_task_tools": _capture_assert,
    }


@pytest.mark.asyncio
async def test_super_agent_mounts_history_tools_by_default() -> None:
    from noesis.agents.super_agent import SuperAgent

    captured_tools: list = []
    captured_worker_tools: list = []
    with patch.multiple(
        "noesis.agents.super_agent", **_super_agent_patches(captured_tools, captured_worker_tools)
    ), patch("noesis.agents.base.get_checkpointer", return_value=MagicMock()):
        agent = SuperAgent()
        await agent._create_compiled_agent(
            user_id="u1",
            session_id="s1",
            model_id=None,
            mcp_tools=None,
            enabled_skills=None,
            file_list=None,
            db=None,
        )

    names = {getattr(tool, "name", "") for tool in captured_tools}
    assert {"search_history", "search_sessions"} <= names
    assert "search_memory" in names  # 与蒸馏层成对

    # task-worker 工具面：隔离 loop 不带 pg 依赖的检索工具（含 search_memory
    # 原有排除项），但保留无 DB 依赖的只读记忆检索替代实例
    worker_names = {getattr(tool, "name", "") for tool in captured_worker_tools}
    assert "search_history" not in worker_names
    assert "search_sessions" not in worker_names
    assert "search_memory" in worker_names


@pytest.mark.asyncio
async def test_general_qa_does_not_mount_history_tools() -> None:
    from noesis.agents.common_qa import GeneralQAAgent

    captured_tools: list = []

    def _capture(**kwargs):
        captured_tools.extend(kwargs.get("tools") or [])
        return MagicMock()

    async def _empty_stream(self, *args, **kwargs):  # noqa: ARG001
        return
        yield  # pragma: no cover

    async def _consume(gen):
        async for _ in gen:
            pass

    with patch(
        "noesis.agents.common_qa.build_kb_search_tools", return_value=[]
    ), patch(
        "noesis.agents.common_qa.build_web_search_tools", return_value=[]
    ), patch(
        "noesis.agents.common_qa.create_noesis_agent", side_effect=_capture
    ), patch.object(
        GeneralQAAgent, "_stream_agent_response", new=_empty_stream
    ):
        current_user = SimpleNamespace(user_id="u1")
        await _consume(
            GeneralQAAgent().run_agent(
                "hi",
                session_id="s1",
                current_user=current_user,
                kb_search_enabled=False,
                web_search_enabled=False,
            )
        )

    names = {getattr(tool, "name", "") for tool in captured_tools}
    assert "search_history" not in names
    assert "search_sessions" not in names
    assert "search_memory" not in names
