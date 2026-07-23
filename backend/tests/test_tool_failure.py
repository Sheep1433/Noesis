"""tool_failure 分类与用户文案单测。"""
from __future__ import annotations

import errno
from unittest.mock import MagicMock

import httpx
import pytest
from langgraph.prebuilt.tool_node import ToolCallRequest
from pydantic import BaseModel, ValidationError

from domain.chat.streaming.tool_failure import (
    ToolFailureCategory,
    ToolInfrastructureError,
    ToolNetworkError,
)
from domain.chat.streaming.tool_failure import (
    DEFAULT_USER_TOOL_ERROR,
    USER_TOOL_ERROR_MESSAGES,
    build_error_tool_message,
    classify_task_tool_output,
    classify_tool_failure,
    failure_to_sse_error_fields,
    format_tool_error_detail,
)


def _request(name: str = "bash") -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": {}, "id": "call_1", "type": "tool_call"},
        tool=None,
        state={},
        runtime=MagicMock(),
    )


@pytest.mark.parametrize(
    ("raw", "forbidden"),
    [
        ("HTTP 403 Forbidden in response body", ToolFailureCategory.PERMISSION_DENIED),
        ("chmod: permission denied", ToolFailureCategory.PERMISSION_DENIED),
        ("documentation: tool not found in registry", ToolFailureCategory.TOOL_NOT_FOUND),
        ("request timeout is configured to 30s", ToolFailureCategory.EXECUTION_TIMEOUT),
        ("subscription canceled successfully", ToolFailureCategory.CANCELLED),
        ("ValidationError mentioned in log excerpt", ToolFailureCategory.INVALID_ARGUMENTS),
    ],
)
def test_free_text_must_not_misclassify(raw: str, forbidden: ToolFailureCategory) -> None:
    failure = classify_tool_failure(None, raw=raw, tool_name="bash")
    assert failure.category == ToolFailureCategory.UNKNOWN
    assert failure.category != forbidden
    assert failure.message_for_user == DEFAULT_USER_TOOL_ERROR


def test_runtime_error_without_cause_is_unknown() -> None:
    failure = classify_tool_failure(RuntimeError("connection refused"), tool_name="bash")
    assert failure.category == ToolFailureCategory.UNKNOWN


def test_runtime_error_internal_error_text_without_typed_exc_is_unknown() -> None:
    failure = classify_tool_failure(
        RuntimeError("[INTERNAL_ERROR] docker pull ubuntu:latest"),
        tool_name="bash",
    )
    assert failure.category == ToolFailureCategory.UNKNOWN


def test_tool_infrastructure_error_explicit() -> None:
    failure = classify_tool_failure(
        ToolInfrastructureError("sandbox not ready"),
        tool_name="bash",
    )
    assert failure.category == ToolFailureCategory.INFRASTRUCTURE
    assert failure.message_for_user == "环境不可用"


def test_httpx_connect_timeout_maps_network_timeout() -> None:
    failure = classify_tool_failure(httpx.ConnectTimeout("connect timeout"), tool_name="web_fetch")
    assert failure.category == ToolFailureCategory.NETWORK_TIMEOUT
    assert failure.retryable is True


def test_wrapped_httpx_connect_error_maps_unreachable() -> None:
    cause = httpx.ConnectError("connection refused")
    try:
        raise RuntimeError("页面抓取失败") from cause
    except RuntimeError as exc:
        failure = classify_tool_failure(exc, tool_name="web_fetch")
    assert failure.category == ToolFailureCategory.NETWORK_UNREACHABLE


def test_oserror_econnrefused() -> None:
    exc = OSError(errno.ECONNREFUSED, "Connection refused")
    failure = classify_tool_failure(exc, tool_name="bash")
    assert failure.category == ToolFailureCategory.NETWORK_UNREACHABLE


def test_validation_error_exception_type() -> None:
    class M(BaseModel):
        x: int

    with pytest.raises(ValidationError) as exc_info:
        M.model_validate({"x": "nope"})
    failure = classify_tool_failure(exc_info.value, tool_name="search")
    assert failure.category == ToolFailureCategory.INVALID_ARGUMENTS
    assert failure.message_for_user == "参数错误"


def test_tool_network_error_explicit() -> None:
    failure = classify_tool_failure(
        ToolNetworkError("host down", category=ToolFailureCategory.NETWORK_UNREACHABLE),
        tool_name="fetch",
    )
    assert failure.category == ToolFailureCategory.NETWORK_UNREACHABLE


def test_parsed_tool_error_header_infrastructure() -> None:
    raw = "[tool_error category=infrastructure retryable=true]\nsandbox not ready"
    failure = classify_tool_failure(None, raw=raw, tool_name="bash")
    assert failure.category == ToolFailureCategory.INFRASTRUCTURE
    assert failure.message_for_user == "环境不可用"


@pytest.mark.parametrize(
    "category",
    list(USER_TOOL_ERROR_MESSAGES.keys()),
)
def test_user_tool_error_messages_fixed_phrases(category: str) -> None:
    raw = {
        "network_unreachable": "[tool_error category=network_unreachable retryable=true]\nx",
        "network_timeout": "[tool_error category=network_timeout retryable=true]\nx",
        "execution_timeout": "[tool_error category=execution_timeout retryable=true]\nx",
        "invalid_arguments": "[tool_error category=invalid_arguments retryable=false]\nx",
        "infrastructure": "[tool_error category=infrastructure retryable=true]\nx",
        "cancelled": "[tool_error category=cancelled retryable=false]\nx",
    }[category]
    failure = classify_tool_failure(None, raw=raw)
    assert failure.message_for_user == USER_TOOL_ERROR_MESSAGES[category]


def test_format_tool_error_detail_truncates() -> None:
    detail = format_tool_error_detail(RuntimeError("x"), raw="y" * 20_000)
    assert len(detail) <= 10_001
    assert detail.endswith("…")
    assert "RuntimeError" in detail


def test_build_error_tool_message_has_structured_content() -> None:
    cause = httpx.ConnectError("connection refused")
    try:
        raise RuntimeError("fail") from cause
    except RuntimeError as exc:
        failure = classify_tool_failure(exc, tool_name="bash")
    msg = build_error_tool_message(_request(), failure)
    assert msg.status == "error"
    assert msg.content.startswith("[tool_error category=network_unreachable")
    assert "fail" in msg.content


def test_failure_to_sse_error_fields() -> None:
    failure = classify_tool_failure(
        None,
        raw="[tool_error category=execution_timeout retryable=true]\ntimed out",
        tool_name="bash",
    )
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


def test_chain_non_unknown_not_overridden_by_raw_text() -> None:
    cause = httpx.ConnectError("refused")
    try:
        raise RuntimeError("wrap") from cause
    except RuntimeError as exc:
        failure = classify_tool_failure(
            exc,
            raw="HTTP 403 Forbidden in response body",
            tool_name="bash",
        )
    assert failure.category == ToolFailureCategory.NETWORK_UNREACHABLE
