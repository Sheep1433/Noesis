import pytest

from noesis.chat.runs import (
    InvalidRunTransition,
    RunSnapshot,
    RunStatus,
    can_transition,
    require_transition,
)
from noesis.chat.delivery.events import HitlRequired, WireFrame
from noesis.services.run_service import RunProjection


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
    assert parts[0]["state"] == "succeeded"
    assert parts[0]["output"] == "ok"
    assert parts[0]["hitl"]["status"] == "approved"


def test_run_projection_preserves_retrieval_results() -> None:
    projection = RunProjection(
        run_id="run-citation",
        user_id="user-1",
        session_id="session-1",
        assistant_message_id="message-1",
        qa_type="SUPER_AGENT_QA",
    )
    result = {
        "source_type": "web",
        "url": "https://example.com/source",
        "title": "Example source",
        "excerpt": "Source excerpt",
    }
    projection.apply(WireFrame("retrieval-results-available", {
        "tool_call_id": "call-search",
        "query": "example",
        "results": [result],
    }))
    projection.apply(WireFrame("text-delta", {
        "part_id": "part-text-1",
        "text_delta": "A supported statement.",
    }))
    parts = projection.persisted_snapshot()["parts"]
    retrieval = next(part for part in parts if part["type"] == "retrieval")
    text = next(part for part in parts if part["type"] == "text")
    assert retrieval["results"][0]["evidence_id"].startswith("ev_")
    assert retrieval["results"][0]["tool_call_ids"] == ["call-search"]
    assert text["content"] == "A supported statement."


def test_run_projection_rolls_back_stream_on_rollback_frame() -> None:
    """stream-rollback 帧（LLM 重试/降级）：失败尝试的部分流式输出不进落库投影。"""
    projection = RunProjection(
        run_id="run-rollback",
        user_id="user-1",
        session_id="session-1",
        assistant_message_id="message-1",
        qa_type="SUPER_AGENT_QA",
    )
    projection.apply(WireFrame("text-delta", {"text_delta": "第一段正文。"}))
    projection.apply(WireFrame("tool-input-available", {
        "tool_call_id": "c1",
        "tool_name": "web_search",
        "input": {"query": "q"},
    }))
    projection.apply(WireFrame("tool-output-available", {
        "tool_call_id": "c1",
        "tool_name": "web_search",
        "output": "ok",
        "status": "success",
    }))
    # 失败尝试的部分流式输出（重试前的正文与思考）
    projection.apply(WireFrame("reasoning-delta", {"text_delta": "Now Phase 6:"}))
    projection.apply(WireFrame("text-delta", {"text_delta": "Phase 3-5 完成。"}))
    # 重试信号：回滚
    projection.apply(WireFrame("stream-rollback", {"message_id": "message-1", "scope": "model_attempt"}))
    # 重试成功后的新输出
    projection.apply(WireFrame("text-delta", {"text_delta": "重试后的正文。"}))

    parts = projection.persisted_snapshot()["parts"]
    types = [part["type"] for part in parts]
    # 失败尝试的 reasoning/text 已丢弃，工具边界与重试后的正文保留
    assert types == ["text", "tool", "text"]
    assert parts[0]["content"] == "第一段正文。"
    assert parts[2]["content"] == "重试后的正文。"
