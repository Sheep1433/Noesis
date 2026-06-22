"""tool_errors 异常层次单测。"""
from __future__ import annotations

import pytest

from domain.chat.streaming.tool_errors import (
    ToolCancelledError,
    ToolFailureCategory,
    ToolFailureError,
    ToolInfrastructureError,
    ToolNetworkError,
    ToolNotFoundError,
    ToolPermissionError,
    ToolTimeoutError,
    ToolValidationError,
    raise_tool_failure,
)


def test_tool_failure_error_defaults() -> None:
    err = ToolFailureError("detail", category=ToolFailureCategory.UNKNOWN)
    assert err.category == ToolFailureCategory.UNKNOWN
    assert err.detail == "detail"
    assert err.retryable is False


def test_subclass_default_category_and_retryable() -> None:
    assert ToolInfrastructureError("x").category == ToolFailureCategory.INFRASTRUCTURE
    assert ToolInfrastructureError("x").retryable is True
    assert ToolTimeoutError("x").category == ToolFailureCategory.EXECUTION_TIMEOUT
    assert ToolValidationError("x").category == ToolFailureCategory.INVALID_ARGUMENTS
    assert ToolValidationError("x").retryable is False
    assert ToolNotFoundError("x").category == ToolFailureCategory.TOOL_NOT_FOUND
    assert ToolPermissionError("x").category == ToolFailureCategory.PERMISSION_DENIED
    assert ToolCancelledError("x").category == ToolFailureCategory.CANCELLED


def test_tool_network_error_category_param() -> None:
    err = ToolNetworkError("timeout", category=ToolFailureCategory.NETWORK_TIMEOUT)
    assert err.category == ToolFailureCategory.NETWORK_TIMEOUT
    assert err.retryable is True


def test_tool_network_error_invalid_category() -> None:
    with pytest.raises(ValueError):
        ToolNetworkError("x", category=ToolFailureCategory.INVALID_ARGUMENTS)


def test_raise_tool_failure_factory() -> None:
    with pytest.raises(ToolInfrastructureError):
        raise_tool_failure(ToolFailureCategory.INFRASTRUCTURE, "sandbox down")

    with pytest.raises(ToolNetworkError) as exc_info:
        raise_tool_failure(ToolFailureCategory.NETWORK_TIMEOUT, "slow")
    assert exc_info.value.category == ToolFailureCategory.NETWORK_TIMEOUT
