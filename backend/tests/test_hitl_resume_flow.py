"""HITL 集成：BaseAgent 将 __interrupt__ 转为 hitl-required + hitl_pending。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.profiles.base_agent import BaseAgent


class _FakeAgent:
    def __init__(self, events):
        self._events = events

    async def astream_events(self, *_args, **_kwargs):
        for ev in self._events:
            yield ev


@pytest.mark.asyncio
async def test_base_agent_emits_hitl_required_then_pending_finish() -> None:
    interrupt = SimpleNamespace(
        id="intr-xyz",
        value={
            "action_requests": [
                {"name": "execute", "args": {"command": "curl https://x"}, "description": "d"}
            ],
            "review_configs": [
                {"action_name": "execute", "allowed_decisions": ["approve", "reject"]}
            ],
        },
    )
    events = [
        {
            "event": "on_chat_model_end",
            "data": {
                "output": SimpleNamespace(
                    tool_calls=[
                        {
                            "id": "call_1",
                            "name": "execute",
                            "args": {"command": "curl https://x"},
                        }
                    ]
                )
            },
        },
        {
            "event": "on_chain_stream",
            "data": {"chunk": {"__interrupt__": (interrupt,)}},
        },
        {"event": "on_chain_end", "data": {}},
    ]
    agent = BaseAgent()
    agent.running_tasks["s1"] = {"cancelled": False}
    out = []
    async for item in agent._stream_agent_response(
        _FakeAgent(events),
        {
            "input": {"messages": []},
            "config": {"configurable": {"thread_id": "s1"}},
            "langfuse_session_id": "s1",
            "qa_type": "SUPER_AGENT_QA",
        },
        "s1",
        "msg-1",
    ):
        out.append(item)

    assert any(i.get("type") == "hitl-required" for i in out)
    hitl = next(i for i in out if i.get("type") == "hitl-required")
    assert hitl["interrupt_id"] == "intr-xyz"
    assert hitl["action_requests"][0]["tool_call_id"] == "call_1"
    assert out[-1] == {"type": "__tw_finish__", "finish_reason": "hitl_pending"}
    assert not any(i.get("finish_reason") == "stop" for i in out)
