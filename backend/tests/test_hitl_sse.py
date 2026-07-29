"""HITL SSE：interrupt 提取与 bridge hitl-required。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from noesis.runtime.hitl import build_hitl_required_event, extract_interrupt_payload, resolve_hitl_kind
from noesis_server.domain.chat.hitl.pending import PendingHitl
from noesis_server.domain.chat.message_builder import AssistantMessageBuilder
from noesis_server.domain.chat.streaming.langgraph_sse import LangGraphSseBridge
from noesis_server.services.qa.service import QaService


def test_extract_interrupt_from_on_chain_stream() -> None:
    interrupt = SimpleNamespace(
        id="intr-1",
        value={
            "action_requests": [{"name": "execute", "args": {"command": "curl x"}, "description": "d"}],
            "review_configs": [{"action_name": "execute", "allowed_decisions": ["approve", "reject"]}],
        },
    )
    event = {
        "event": "on_chain_stream",
        "data": {"chunk": {"__interrupt__": (interrupt,)}},
    }
    got = extract_interrupt_payload(event)
    assert got is not None
    assert got[0] == "intr-1"


def test_build_hitl_required_enriches_tool_call_id() -> None:
    value = {
        "action_requests": [{"name": "execute", "args": {"command": "curl x"}}],
        "review_configs": [{"action_name": "execute", "allowed_decisions": ["approve", "reject"]}],
    }
    ev = build_hitl_required_event(
        interrupt_id="i1",
        hitl_value=value,
        session_id="s1",
        message_id="m1",
        tool_calls=[{"id": "call_abc", "name": "execute", "args": {"command": "curl x"}}],
    )
    assert ev["kind"] == "approval"
    assert ev["action_requests"][0]["tool_call_id"] == "call_abc"
    assert resolve_hitl_kind([{"name": "ask_user"}]) == "clarification"


def test_bridge_emits_hitl_required_and_tool_parts() -> None:
    bridge = LangGraphSseBridge("s1", assistant_message_id="aid-1")
    builder = AssistantMessageBuilder(session_id="s1", message_id="aid-1")
    ctx: dict = {}
    payload = {
        "type": "hitl-required",
        "interrupt_id": "i1",
        "kind": "approval",
        "action_requests": [
            {
                "name": "execute",
                "args": {"command": "curl https://example.com"},
                "tool_call_id": "call_1",
                "description": "net",
            }
        ],
        "review_configs": [],
        "expires_at": 1,
    }
    lines = bridge.process_item(payload, builder, ctx)
    joined = "".join(lines)
    assert "hitl-required" in joined
    assert "tool-input-available" in joined
    assert bridge.last_hitl_payload is not None
    assert builder.to_dict()["parts"]
    assert builder.to_dict()["parts"][0]["hitl"]["status"] == "pending"
    assert builder.to_dict()["parts"][0]["state"] == "approval_pending"

    finish_lines = bridge.process_item(
        {"type": "__tw_finish__", "finish_reason": "hitl_pending"},
        builder,
        ctx,
    )
    assert any("hitl_pending" in line for line in finish_lines)
    assert bridge.last_finish_reason == "hitl_pending"


def test_hitl_resume_callback_uuid_reuses_model_tool_call_id() -> None:
    bridge = LangGraphSseBridge("s-resume", assistant_message_id="aid-resume")
    builder = AssistantMessageBuilder(session_id="s-resume", message_id="aid-resume")
    ctx: dict = {}
    bridge.process_item(
        {
            "type": "hitl-required",
            "interrupt_id": "interrupt-1",
            "kind": "approval",
            "action_requests": [{
                "name": "execute",
                "args": {"command": "curl example.com", "timeout": 15},
                "tool_call_id": "call-model-1",
            }],
            "review_configs": [],
        },
        builder,
        ctx,
    )
    builder.update_tool_hitl("call-model-1", {"status": "approved"})

    start_lines = bridge.process_item(
        {
            "event": "on_tool_start",
            "name": "execute",
            "run_id": "callback-run-uuid",
            "data": {"input": {"command": "curl example.com", "timeout": 15}},
        },
        builder,
        ctx,
    )
    end_lines = bridge.process_item(
        {
            "event": "on_tool_end",
            "name": "execute",
            "run_id": "callback-run-uuid",
            "data": {"output": "ok"},
        },
        builder,
        ctx,
    )

    assert '\"tool_call_id\": \"call-model-1\"' in "".join(start_lines + end_lines)
    parts = builder.to_dict()["parts"]
    assert len(parts) == 1
    assert parts[0]["tool_call_id"] == "call-model-1"
    assert parts[0]["status"] == "success"
    assert parts[0]["output"] == "ok"


def test_child_hitl_keeps_parent_task_after_task_stack_is_no_longer_active() -> None:
    bridge = LangGraphSseBridge("s-child", assistant_message_id="aid-child")
    builder = AssistantMessageBuilder(session_id="s-child", message_id="aid-child")
    ctx: dict = {}
    task_run = "run-task"
    task_lines = bridge.process_item(
        {
            "event": "on_tool_start",
            "name": "task",
            "run_id": task_run,
            "parent_ids": [],
            "data": {"input": {"description": "research"}},
        },
        builder,
        ctx,
    )
    task_call_id = next(
        part["tool_call_id"]
        for part in (
            __import__("json").loads(line.removeprefix("data: "))
            for frame in task_lines
            for line in frame.splitlines()
            if line.startswith("data: {")
        )
        if part.get("type") == "tool-input-available"
    )
    # 模拟 task stack 已收口，但 LangGraph parent_ids 仍能证明 interrupt 属于该 task。
    ctx["task_tool_call_stack"] = []
    payload = {
        "type": "hitl-required",
        "interrupt_id": "i-child",
        "kind": "approval",
        "parent_ids": [task_run],
        "action_requests": [
            {"name": "execute", "args": {"command": "curl x"}, "tool_call_id": "call-curl"}
        ],
        "review_configs": [],
    }
    lines = bridge.process_item(payload, builder, ctx)
    joined = "".join(lines)
    assert f'"parent_task_call_id": "{task_call_id}"' in joined
    execute_part = next(
        part for part in builder.to_dict()["parts"] if part.get("tool_call_id") == "call-curl"
    )
    assert execute_part["parent_task_call_id"] == task_call_id


@pytest.mark.asyncio
async def test_exec_hitl_resume_checks_expiry_without_initialization_error() -> None:
    """直接执行真实 resume 入口，避免 RunService mock 掩盖 import/初始化错误。"""
    pending = PendingHitl(
        interrupt_id="interrupt-expired",
        session_id="session-expired",
        user_id="1",
        assistant_message_id="assistant-expired",
        expires_at=1,
        kind="approval",
    )

    lines = [
        line
        async for line in QaService.exec_hitl_resume(
            pending=pending,
            decisions=[{"type": "approve"}],
            grant_scope="once",
            current_user=SimpleNamespace(user_id=1),
            db=SimpleNamespace(),
        )
    ]

    joined = "".join(lines)
    assert "等待确认已超时" in joined
    assert "[DONE]" in joined
