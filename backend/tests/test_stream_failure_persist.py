"""stream_failure_notice 与 assistant 终态落库辅助函数。"""
from __future__ import annotations

import pytest

from utils.stream_failure_notice import (
    append_stream_failure_notice_to_content,
    get_stream_failure_notice_text,
    is_recursion_limit_error,
)


def test_is_recursion_limit_error() -> None:
    assert is_recursion_limit_error("Recursion limit of 40 reached")
    assert is_recursion_limit_error("已达到最大处理步数，任务已自动停止。")
    assert not is_recursion_limit_error("network timeout")


def test_append_notice_with_existing_prose() -> None:
    content = {
        "version": 1,
        "parts": [{"type": "text", "content": "已有正文", "status": "completed"}],
    }
    out = append_stream_failure_notice_to_content(
        content,
        "Recursion limit of 40 reached",
    )
    assert len(out["parts"]) == 2
    assert "已达到最大处理步数" in out["parts"][-1]["content"]


def test_append_notice_without_prose() -> None:
    content = {"version": 1, "parts": []}
    out = append_stream_failure_notice_to_content(
        content,
        "Recursion limit of 40 reached",
    )
    assert len(out["parts"]) == 1
    assert out["parts"][0]["content"] == get_stream_failure_notice_text(
        "Recursion limit of 40 reached",
        False,
    )


def test_assistant_status_for_finish() -> None:
    from services.qa_service import _assistant_status_for_finish

    assert _assistant_status_for_finish("error") == "error"
    assert _assistant_status_for_finish("stop") == "completed"


def test_build_assistant_persist_extra_includes_qa_type() -> None:
    from services.qa_service import _build_assistant_persist_extra
    from utils.langgraph_sse_bridge import LangGraphSseBridge

    bridge = LangGraphSseBridge("s1")
    bridge.last_finish_reason = "error"
    bridge.last_error_message = "已达到最大处理步数"
    bridge.last_finish_usage = {"input_tokens": 1, "output_tokens": 2}

    extra = _build_assistant_persist_extra(qa_type="DEEP_RESEARCH_QA", bridge=bridge)
    assert extra["qa_type"] == "DEEP_RESEARCH_QA"
    assert extra["finish_reason"] == "error"
    assert "已达到最大处理步数" in extra["error_message"]
