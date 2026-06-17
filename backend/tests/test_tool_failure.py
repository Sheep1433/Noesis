"""tool_failure 分类与用户文案单测。"""
from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from domain.chat.streaming.tool_failure import (
    DEFAULT_USER_TOOL_ERROR,
    ToolFailureCategory,
    USER_TOOL_ERROR_MESSAGES,
    build_error_tool_message,
    classify_task_tool_output,
    classify_tool_failure,
    failure_to_sse_error_fields,
)
from langgraph.prebuilt.tool_node import ToolCallRequest
from unittest.mock import MagicMock


def _request(name: str = "bash") -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": {}, "id": "call_1", "type": "tool_call"},
        tool=None,
        state={},
        runtime=MagicMock(),
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Connection refused", ToolFailureCategory.NETWORK_UNREACHABLE),
        ("ECONNREFUSED on port 22", ToolFailureCategory.NETWORK_UNREACHABLE),
        ("ConnectTimeout: connection timed out", ToolFailureCategory.NETWORK_TIMEOUT),
        ("socket hang up", ToolFailureCategory.NETWORK_TIMEOUT),
        ("command timed out after 30s", ToolFailureCategory.EXECUTION_TIMEOUT),
        ("ValidationError: field required", ToolFailureCategory.INVALID_ARGUMENTS),
        ("tool not found: foo", ToolFailureCategory.TOOL_NOT_FOUND),
        ("Permission denied", ToolFailureCategory.PERMISSION_DENIED),
        ("[INTERNAL_ERROR] Docker image missing", ToolFailureCategory.INFRASTRUCTURE),
        ("Task failed. bash errored", ToolFailureCategory.SUBAGENT_FAILURE),
        ("user stop requested", ToolFailureCategory.CANCELLED),
        ("something weird happened", ToolFailureCategory.UNKNOWN),
    ],
)
def test_classify_tool_failure_samples(raw: str, expected: ToolFailureCategory) -> None:
    failure = classify_tool_failure(None, raw=raw, tool_name="bash")
    assert failure.category == expected


def test_validation_error_exception_type() -> None:
    class M(BaseModel):
        x: int

    with pytest.raises(ValidationError) as exc_info:
        M.model_validate({"x": "nope"})
    failure = classify_tool_failure(exc_info.value, tool_name="search")
    assert failure.category == ToolFailureCategory.INVALID_ARGUMENTS
    assert failure.message_for_user == "参数错误"


@pytest.mark.parametrize(
    "category",
    list(USER_TOOL_ERROR_MESSAGES.keys()),
)
def test_user_tool_error_messages_fixed_phrases(category: str) -> None:
    failure = classify_tool_failure(None, raw=_sample_for_category(category))
    assert failure.message_for_user == USER_TOOL_ERROR_MESSAGES[category]


def _sample_for_category(category: str) -> str:
    samples = {
        "network_unreachable": "connection refused",
        "network_timeout": "connection timed out",
        "execution_timeout": "execution timed out",
        "invalid_arguments": "ValidationError: bad field",
        "infrastructure": "[INTERNAL_ERROR] sandbox not ready",
        "cancelled": "user stop",
    }
    return samples[category]


def test_unknown_fallback_user_message() -> None:
    failure = classify_tool_failure(None, raw="opaque internal fault")
    assert failure.category == ToolFailureCategory.UNKNOWN
    assert failure.message_for_user == DEFAULT_USER_TOOL_ERROR


def test_build_error_tool_message_has_structured_content() -> None:
    failure = classify_tool_failure(RuntimeError("connection refused"), tool_name="bash")
    msg = build_error_tool_message(_request(), failure)
    assert msg.status == "error"
    assert msg.content.startswith("[tool_error category=network_unreachable")
    assert "connection refused" in msg.content


def test_failure_to_sse_error_fields() -> None:
    failure = classify_tool_failure(None, raw="command timed out", tool_name="bash")
    fields = failure_to_sse_error_fields(failure)
    assert fields["error"] == "执行超时"
    assert fields["errorCategory"] == "execution_timeout"


def test_classify_task_tool_output_success() -> None:
    assert classify_task_tool_output("Task Succeeded. Result: done") is None


def test_classify_task_tool_output_failure() -> None:
    failure = classify_task_tool_output("Task failed. tool bash broke")
    assert failure is not None
    assert failure.category == ToolFailureCategory.SUBAGENT_FAILURE
    assert failure.message_for_user == DEFAULT_USER_TOOL_ERROR


def test_passthrough_tool_error_prefix() -> None:
    raw = (
        "[tool_error category=invalid_arguments retryable=false]\n"
        "Tool 'search' failed: bad query"
    )
    failure = classify_tool_failure(None, raw=raw, tool_name="search")
    assert failure.category == ToolFailureCategory.INVALID_ARGUMENTS
    assert failure.retryable is False
