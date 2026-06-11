"""cancel_task 与 run_agent 均使用 session_id 作为 running_tasks key。"""
from __future__ import annotations

import pytest

from agent.common_react_agent import GeneralQAAgent
from agent.deep_research_agent import DeepResearchAgent
from agent.fault_operation_agent import FaultOperationAgent


@pytest.mark.parametrize(
    "agent_cls",
    [GeneralQAAgent, DeepResearchAgent, FaultOperationAgent],
)
@pytest.mark.asyncio
async def test_cancel_task_matches_session_id(agent_cls) -> None:
    agent = agent_cls()
    session_id = "sess-cancel-1"
    agent.running_tasks[session_id] = {"cancelled": False}

    assert await agent.cancel_task(session_id) is True
    assert agent.running_tasks[session_id]["cancelled"] is True

    del agent.running_tasks[session_id]
