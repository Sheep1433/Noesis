"""用户主动停止：stop_chat 收尾与 stream_failure_notice 对齐。"""
from __future__ import annotations

from noesis.domain.chat.streaming.failure_notice import (
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
