"""骨架行构造器契约：主链路创建 / 子 Agent launch / followup 三处共用的固定字段在此钉住。"""

from noesis.chat.runs.skeleton import (
    build_assistant_skeleton_row,
    build_queued_run_row,
    build_user_message_row,
)

NOW = 1_700_000_000_000


def test_user_message_row_strips_text() -> None:
    row = build_user_message_row(
        message_id="m1",
        session_id="s1",
        user_id="u1",
        text="  你好  ",
        extra={"origin": "subagent"},
        message_sequence=3,
        created_at=NOW,
    )
    assert row.role == "user"
    assert row.status == "completed"
    assert row.parent_id is None
    assert "你好" in str(row.content)
    assert "  你好" not in str(row.content)


def test_assistant_skeleton_row_defaults() -> None:
    row = build_assistant_skeleton_row(
        message_id="m2",
        session_id="s1",
        user_id="u1",
        parent_id="m1",
        extra={"run_id": "r1"},
        message_sequence=4,
        created_at=NOW,
    )
    assert row.role == "assistant"
    assert row.status == "streaming"
    assert row.content == {"parts": []}
    assert row.parent_id == "m1"


def test_queued_run_row_defaults() -> None:
    row = build_queued_run_row(
        run_id="r1",
        user_id="u1",
        session_id="s1",
        assistant_message_id="m2",
        client_request_id="c1",
        request_digest="d1",
        qa_type="SUPER_AGENT_QA",
        origin="subagent",
        created_at=NOW,
    )
    assert row.status == "queued"
    assert row.last_sequence == 0
    assert row.attempt_id == 1
    assert row.snapshot == {"parts": []}
    assert row.owner_instance_id is None
    assert row.launch_payload is None
    assert row.updated_at == NOW
