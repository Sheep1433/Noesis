from __future__ import annotations

import pytest

from noesis.chat.delivery.events import (
    HitlRequired,
    RunAborted,
    RunCompleted,
    RunError,
    WireFrame,
)
from noesis.chat.message_builder import AssistantMessageBuilder
from noesis.chat.tool_state import (
    ToolState,
    can_transition_tool_state,
    derive_tool_state,
)
from noesis.services.run_service import RunProjection


@pytest.mark.parametrize(
    ("status", "outcome", "category", "timed_out", "expected"),
    [
        ("success", "ok", None, False, ToolState.SUCCEEDED),
        ("success", "empty", None, False, ToolState.SUCCEEDED),
        ("success", "command_failed", None, False, ToolState.FAILED),
        ("success", "timed_out", None, True, ToolState.TIMED_OUT),
        ("error", None, "network_unreachable", False, ToolState.FAILED),
        ("error", None, "execution_timeout", False, ToolState.TIMED_OUT),
    ],
)
def test_tool_state_matrix(status, outcome, category, timed_out, expected) -> None:
    assert derive_tool_state(
        status=status,
        outcome=outcome,
        error_category=category,
        timed_out=timed_out,
    ) == expected


def test_terminal_tool_state_cannot_regress() -> None:
    assert can_transition_tool_state(ToolState.RUNNING, ToolState.FAILED)
    assert not can_transition_tool_state(ToolState.FAILED, ToolState.RUNNING)
    builder = AssistantMessageBuilder()
    builder.append_tool("web_fetch", {}, "call-1")
    builder.append_tool_output(
        "web_fetch",
        "",
        "call-1",
        status="error",
        state=ToolState.FAILED,
        error_category="network_unreachable",
    )
    builder.append_tool("web_fetch", {}, "call-1", state=ToolState.RUNNING)
    part = builder.to_dict()["parts"][0]
    assert part["state"] == "failed"


def _projection() -> RunProjection:
    return RunProjection(
        run_id="run-1",
        user_id="user-1",
        session_id="session-1",
        assistant_message_id="message-1",
        qa_type="SUPER_AGENT_QA",
    )


@pytest.mark.parametrize(
    ("terminal_event", "expected"),
    [
        (RunCompleted(), "cancelled"),
        (RunAborted(reason="stopped"), "cancelled"),
        (RunError(message="执行失败"), "failed"),
        (RunError(message="执行超时", finish_reason="timeout"), "timed_out"),
    ],
)
def test_run_terminal_reconciles_running_tools(terminal_event, expected) -> None:
    projection = _projection()
    projection.apply(WireFrame("tool-input-available", {
        "tool_call_id": "call-1",
        "name": "execute",
        "input": {"command": "sleep 10"},
        "state": "running",
    }))
    projection.apply(terminal_event)
    assert projection.builder.to_dict()["parts"][0]["state"] == expected


def test_hitl_keeps_action_and_parent_pending_but_cancels_other_running_tools() -> None:
    projection = _projection()
    projection.apply(WireFrame("tool-input-available", {
        "tool_call_id": "task-1", "name": "task", "input": {}, "state": "running",
    }))
    projection.apply(WireFrame("tool-input-available", {
        "tool_call_id": "call-action",
        "name": "execute",
        "input": {"command": "curl example.com"},
        "parent_task_call_id": "task-1",
        "state": "running",
    }))
    projection.apply(WireFrame("tool-input-available", {
        "tool_call_id": "call-other", "name": "web_fetch", "input": {}, "state": "running",
    }))
    projection.apply(HitlRequired({
        "interrupt_id": "interrupt-1",
        "kind": "approval",
        "action_requests": [{"tool_call_id": "call-action", "name": "execute", "args": {}}],
    }))
    states = {
        part["tool_call_id"]: part["state"]
        for part in projection.builder.to_dict()["parts"]
    }
    assert states == {
        "task-1": "approval_pending",
        "call-action": "approval_pending",
        "call-other": "cancelled",
    }


def test_late_completed_cannot_overwrite_run_error() -> None:
    projection = _projection()
    projection.apply(RunError(message="执行环境不可用"))
    assert projection.apply(RunCompleted()) is False
    assert projection.status.value == "error"
