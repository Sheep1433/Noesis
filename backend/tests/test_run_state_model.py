import pytest

from noesis_server.domain.chat.runs import (
    InvalidRunTransition,
    RunSnapshot,
    RunStatus,
    can_transition,
    require_transition,
)
from noesis_server.domain.chat.delivery.events import HitlRequired, WireFrame
from noesis_server.services.run_service import RunProjection


def test_run_state_machine_accepts_retry_and_hitl_resume() -> None:
    assert can_transition(RunStatus.QUEUED, RunStatus.RUNNING)
    assert can_transition(RunStatus.RUNNING, RunStatus.RETRYING)
    assert can_transition(RunStatus.RETRYING, RunStatus.RUNNING)
    assert can_transition(RunStatus.RUNNING, RunStatus.HITL_PENDING)
    assert can_transition(RunStatus.HITL_PENDING, RunStatus.RUNNING)


@pytest.mark.parametrize("terminal", ["completed", "partial", "error", "interrupted"])
def test_terminal_state_cannot_be_overwritten(terminal: str) -> None:
    status = RunStatus(terminal)
    assert can_transition(status, status)
    with pytest.raises(InvalidRunTransition):
        require_transition(status, RunStatus.RUNNING)


def test_snapshot_uses_wire_contract_names() -> None:
    snapshot = RunSnapshot(
        run_id="run-1",
        user_id="user-1",
        session_id="session-1",
        assistant_message_id="message-1",
        qa_type="COMMON_QA",
        origin="web",
        status=RunStatus.RETRYING,
        sequence=7,
        attempt_id=2,
        parts=({"type": "text", "text": "部分结果"},),
        retry_attempt=1,
        retry_max=3,
    )

    payload = snapshot.to_dict()

    assert payload["snapshot_sequence"] == 7
    assert payload["status"] == "retrying"
    assert payload["attempt_id"] == 2
    assert payload["content"]["parts"][0]["text"] == "部分结果"


def test_hitl_resume_replay_updates_original_tool_part() -> None:
    projection = RunProjection(
        run_id="run-1",
        user_id="user-1",
        session_id="session-1",
        assistant_message_id="message-1",
        qa_type="SUPER_AGENT_QA",
    )
    tool_input = {
        "tool_call_id": "call-1",
        "name": "execute",
        "input": {"command": "curl example.com"},
    }
    projection.apply(WireFrame("tool-call-start", tool_input))
    projection.apply(WireFrame("tool-input-available", tool_input))
    projection.apply(HitlRequired({
        "interrupt_id": "interrupt-1",
        "kind": "approval",
        "action_requests": [{
            "tool_call_id": "call-1",
            "name": "execute",
            "args": tool_input["input"],
        }],
    }))
    projection.apply_hitl_decisions([{"type": "approve"}])
    projection.begin_hitl_resume()

    # LangGraph resume 可能重放同一 tool start；投影必须更新旧块而不是追加。
    projection.apply(WireFrame("tool-input-available", tool_input))
    projection.apply(WireFrame("tool-output-available", {
        "tool_call_id": "call-1",
        "name": "execute",
        "output": "ok",
        "status": "success",
    }))

    parts = projection.builder.to_dict()["parts"]
    assert len(parts) == 1
    assert parts[0]["tool_call_id"] == "call-1"
    assert parts[0]["status"] == "success"
    assert parts[0]["output"] == "ok"
    assert parts[0]["hitl"]["status"] == "approved"
