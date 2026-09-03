"""tool_failure 分类与单份短文案单测（模型/入库/展示三通道同源）。"""
from __future__ import annotations

import errno
from unittest.mock import MagicMock

import httpx
import pytest
from langgraph.prebuilt.tool_node import ToolCallRequest
from pydantic import BaseModel, ValidationError

from noesis.errors.tool_failure import (
    DEFAULT_USER_TOOL_ERROR,
    ToolFailureCategory,
    ToolInfrastructureError,
    ToolNetworkError,
    build_error_tool_message,
    classify_task_tool_output,
    classify_tool_failure,
    failure_to_sse_error_fields,
    format_tool_error_detail,
    strip_error_prefix,
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
    assert failure.text == DEFAULT_USER_TOOL_ERROR


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
    # 可重试类：固定短文案 + 统一后缀（防模型盲目重试由「不可重试不带后缀」承担）
    assert failure.text == "环境暂时不可用，可稍后重试"


def test_httpx_connect_timeout_maps_network_timeout() -> None:
    failure = classify_tool_failure(httpx.ConnectTimeout("connect timeout"), tool_name="web_fetch")
    assert failure.category == ToolFailureCategory.NETWORK_TIMEOUT
    assert failure.retryable is True
    assert failure.text == "网络超时，可稍后重试"


def test_wrapped_httpx_connect_error_maps_unreachable() -> None:
    cause = httpx.ConnectError("connection refused")
    try:
        raise RuntimeError("页面抓取失败") from cause
    except RuntimeError as exc:
        failure = classify_tool_failure(exc, tool_name="web_fetch")
    assert failure.category == ToolFailureCategory.NETWORK_UNREACHABLE
    assert failure.text == "连接失败，可稍后重试"


def test_oserror_econnrefused() -> None:
    exc = OSError(errno.ECONNREFUSED, "Connection refused")
    failure = classify_tool_failure(exc, tool_name="bash")
    assert failure.category == ToolFailureCategory.NETWORK_UNREACHABLE


def test_validation_error_structured_one_liner() -> None:
    """pydantic ValidationError → 结构化一行，不灌 str(exc) 全文 dump。"""

    class M(BaseModel):
        x: int

    with pytest.raises(ValidationError) as exc_info:
        M.model_validate({"y": "nope"})
    failure = classify_tool_failure(exc_info.value, tool_name="search")
    assert failure.category == ToolFailureCategory.INVALID_ARGUMENTS
    assert failure.retryable is False
    # 不可重试类不带后缀；文案 = 字段: 错误（传入字段：…）
    assert failure.text == "x: Field required（传入字段：y）"
    assert "errors.pydantic.dev" not in failure.text
    assert "input_value" not in failure.text


def test_validation_error_multi_field_bounded() -> None:
    class M(BaseModel):
        a: int
        b: int
        c: int

    with pytest.raises(ValidationError) as exc_info:
        M.model_validate({"z": 1})
    failure = classify_tool_failure(exc_info.value, tool_name="write_file")
    text = failure.text
    assert "a: Field required" in text
    assert "；" in text  # 多字段用分号连接
    assert len(text) <= 600


def test_tool_network_error_explicit() -> None:
    failure = classify_tool_failure(
        ToolNetworkError("host down", category=ToolFailureCategory.NETWORK_UNREACHABLE),
        tool_name="fetch",
    )
    assert failure.category == ToolFailureCategory.NETWORK_UNREACHABLE
    assert failure.text == "连接失败，可稍后重试"


def test_parsed_tool_error_header_infrastructure() -> None:
    raw = "[tool_error category=infrastructure retryable=true]\nsandbox not ready"
    failure = classify_tool_failure(None, raw=raw, tool_name="bash")
    assert failure.category == ToolFailureCategory.INFRASTRUCTURE
    assert failure.text == "环境暂时不可用，可稍后重试"


def test_file_tool_usage_error_carries_reason() -> None:
    """deepagents 文件后端 result 级错误（稳定文案契约）→ 参数错误 + 具体原因。"""
    failure = classify_tool_failure(
        None,
        raw="Error: File '/workspace/report.md': Line offset 780 exceeds file length (668 lines)",
        tool_name="read_file",
    )
    assert failure.category == ToolFailureCategory.INVALID_ARGUMENTS
    # 具体原因直接作为短文案（模型与用户都可定位），不再加「参数错误：」前缀包装
    assert failure.text.startswith("File '/workspace/report.md': Line offset 780 exceeds file length")


def test_format_tool_error_detail_truncates() -> None:
    detail = format_tool_error_detail(RuntimeError("x"), raw="y" * 20_000)
    assert len(detail) <= 10_001
    assert detail.endswith("…")
    assert "RuntimeError" in detail


def test_short_text_bounded_to_600() -> None:
    failure = classify_tool_failure(
        None,
        raw="Error: " + "z" * 5_000,
        tool_name="bash",
    )
    assert len(failure.text) <= 600


def test_build_error_tool_message_short_content() -> None:
    cause = httpx.ConnectError("connection refused")
    try:
        raise RuntimeError("fail") from cause
    except RuntimeError as exc:
        failure = classify_tool_failure(exc, tool_name="bash")
    msg = build_error_tool_message(_request(), failure)
    assert msg.status == "error"
    assert msg.content == "Error: 连接失败，可稍后重试"
    assert msg.additional_kwargs["errorCategory"] == "network_unreachable"
    assert msg.additional_kwargs["retryable"] is True


def test_failure_to_sse_error_fields() -> None:
    failure = classify_tool_failure(
        None,
        raw="[tool_error category=execution_timeout retryable=true]\ntimed out",
        tool_name="bash",
    )
    fields = failure_to_sse_error_fields(failure)
    assert fields["error"] == "执行超时，可稍后重试"
    assert fields["errorCategory"] == "execution_timeout"


def test_classify_task_tool_output_success() -> None:
    assert classify_task_tool_output("Task Succeeded. Result: done") is None


def test_classify_task_tool_output_failure() -> None:
    failure = classify_task_tool_output("Task failed. tool bash broke")
    assert failure is not None
    assert failure.category == ToolFailureCategory.SUBAGENT_FAILURE
    # task 包装文本首行即权威文案
    assert failure.text == "Task failed. tool bash broke"


def test_passthrough_tool_error_prefix() -> None:
    raw = (
        "[tool_error category=invalid_arguments retryable=false]\n"
        "Tool 'search' failed: bad query"
    )
    failure = classify_tool_failure(None, raw=raw, tool_name="search")
    assert failure.category == ToolFailureCategory.INVALID_ARGUMENTS
    assert failure.retryable is False
    assert failure.text == "Tool 'search' failed: bad query"


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


def test_strip_error_prefix() -> None:
    assert strip_error_prefix("Error: 连接失败") == "连接失败"
    assert strip_error_prefix("error: lower") == "lower"
    assert strip_error_prefix("连接失败") == "连接失败"
    assert strip_error_prefix("") == ""
