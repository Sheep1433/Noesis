"""用户主动停止：stop_chat 收尾与 stream_failure_notice 对齐。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services.qa_service import QaService, _ActiveStreamState
from domain.chat.message_builder import AssistantMessageBuilder
from domain.chat.streaming.failure_notice import (
    USER_STOP_NOTICE_PLAIN,
    USER_STOP_TOOL_ERROR,
    append_disconnect_partial_content,
    append_user_stop_notice_to_content,
)


def test_append_user_stop_notice_running_tool() -> None:
    content = {
        "version": 1,
        "parts": [
            {
                "type": "tool",
                "name": "search",
                "tool_call_id": "tc1",
                "status": "running",
                "input": {},
            },
        ],
    }
    out = append_user_stop_notice_to_content(content)
    assert out["parts"][0]["status"] == "error"
    assert out["parts"][0]["error"] == USER_STOP_TOOL_ERROR
    assert any(
        USER_STOP_NOTICE_PLAIN in str(p.get("content", ""))
        for p in out["parts"]
        if p.get("type") == "text"
    )


def test_append_user_stop_notice_with_prose() -> None:
    content = {
        "version": 1,
        "parts": [{"type": "text", "content": "部分回答", "status": "completed"}],
    }
    out = append_user_stop_notice_to_content(content)
    assert len(out["parts"]) == 2
    assert "本轮回复已被用户中断" in out["parts"][-1]["content"]


def test_disconnect_partial_no_user_stop_notice() -> None:
    content = {
        "version": 1,
        "parts": [
            {"type": "tool", "name": "search", "status": "running", "input": {}},
        ],
    }
    out = append_disconnect_partial_content(content)
    assert out["parts"][0]["error"] == "工具未返回结果"
    assert not any(p.get("type") == "text" for p in out["parts"])
    assert USER_STOP_NOTICE_PLAIN not in str(out)


@pytest.mark.asyncio
async def test_stop_chat_sets_user_stopped_and_persists() -> None:
    session_id = "sess-stop-1"
    builder = AssistantMessageBuilder(session_id=session_id, message_id="msg-1")
    builder.append_tool("search", {"q": "x"}, tool_call_id="tc1")
    ctx = {"text_buffer": "尾段", "_assistant_db_id": "msg-1"}

    QaService._active_streams[session_id] = _ActiveStreamState(
        builder=builder,
        ctx=ctx,
        qa_type="COMMON_QA",
    )

    mock_persist = AsyncMock()
    mock_cancel = AsyncMock(return_value=True)
    user = SimpleNamespace(user_id="u1")

    try:
        with patch("services.qa.service._persist_assistant", mock_persist):
            with patch("services.qa.service.common_agent.cancel_task", mock_cancel):
                ok, msg = await QaService.stop_chat(session_id, "COMMON_QA", user)

        assert ok is True
        assert msg == "停止成功"
        assert ctx.get("user_stopped") is True
        assert QaService._active_streams[session_id].user_stopped is True
        mock_persist.assert_awaited_once()
        content_dict = mock_persist.await_args.args[0]
        kwargs = mock_persist.await_args.kwargs
        assert kwargs["status"] == "partial"
        assert kwargs["extra"]["finish_reason"] == "stopped"
        tool_part = next(p for p in content_dict["parts"] if p.get("type") == "tool")
        assert tool_part["status"] == "error"
        assert tool_part["error"] == USER_STOP_TOOL_ERROR
        mock_cancel.assert_awaited_once_with(session_id)
    finally:
        QaService._active_streams.pop(session_id, None)
