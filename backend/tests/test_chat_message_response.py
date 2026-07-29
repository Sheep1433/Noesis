from types import SimpleNamespace

from noesis_server.api.chat_api import _message_to_response


def test_assistant_history_response_normalizes_missing_tool_state() -> None:
    message = SimpleNamespace(
        id="message-1",
        session_id="session-1",
        parent_id=None,
        user_id="1",
        role="assistant",
        content={
            "parts": [
                {
                    "type": "tool",
                    "name": "execute",
                    "tool_call_id": "call-1",
                    "input": {"command": "echo ok"},
                    "output": "ok",
                    "status": "success",
                }
            ]
        },
        extra=None,
        status="completed",
        message_sequence=2,
        created_at=1,
    )

    response = _message_to_response(message)

    assert response.content["parts"][0]["state"] == "succeeded"


def test_assistant_history_response_restores_pending_approval_state() -> None:
    message = SimpleNamespace(
        id="message-2",
        session_id="session-1",
        parent_id=None,
        user_id="1",
        role="assistant",
        content={
            "parts": [
                {
                    "type": "tool",
                    "name": "execute",
                    "status": "running",
                    "hitl": {"status": "pending", "interrupt_id": "interrupt-1"},
                }
            ]
        },
        extra=None,
        status="streaming",
        message_sequence=2,
        created_at=1,
    )

    response = _message_to_response(message)

    assert response.content["parts"][0]["state"] == "approval_pending"


def test_assistant_history_response_replaces_invalid_tool_state() -> None:
    message = SimpleNamespace(
        id="message-3",
        session_id="session-1",
        parent_id=None,
        user_id="1",
        role="assistant",
        content={
            "parts": [
                {
                    "type": "tool",
                    "name": "execute",
                    "status": "error",
                    "state": "completed",
                }
            ]
        },
        extra=None,
        status="error",
        message_sequence=2,
        created_at=1,
    )

    response = _message_to_response(message)

    assert response.content["parts"][0]["state"] == "failed"
