"""工具失败 category 枚举与显式异常层次。"""

from __future__ import annotations

from enum import Enum
from typing import NoReturn


class ToolFailureCategory(str, Enum):
    NETWORK_UNREACHABLE = "network_unreachable"
    NETWORK_TIMEOUT = "network_timeout"
    EXECUTION_TIMEOUT = "execution_timeout"
    INVALID_ARGUMENTS = "invalid_arguments"
    TOOL_NOT_FOUND = "tool_not_found"
    PERMISSION_DENIED = "permission_denied"
    INFRASTRUCTURE = "infrastructure"
    SUBAGENT_FAILURE = "subagent_failure"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ToolFailureError(Exception):
    category: ToolFailureCategory = ToolFailureCategory.UNKNOWN
    detail: str
    retryable: bool = False

    def __init__(
        self,
        detail: str,
        *,
        category: ToolFailureCategory | None = None,
        retryable: bool | None = None,
    ) -> None:
        self.detail = detail
        if category is not None:
            self.category = category
        if retryable is not None:
            self.retryable = retryable
        super().__init__(detail)


class ToolInfrastructureError(ToolFailureError):
    category = ToolFailureCategory.INFRASTRUCTURE
    retryable = True


class ToolTimeoutError(ToolFailureError):
    category = ToolFailureCategory.EXECUTION_TIMEOUT
    retryable = True


class ToolNetworkError(ToolFailureError):
    retryable = True

    def __init__(
        self,
        detail: str,
        *,
        category: ToolFailureCategory | None = None,
        retryable: bool | None = None,
    ) -> None:
        net_category = category or ToolFailureCategory.NETWORK_UNREACHABLE
        if net_category not in (
            ToolFailureCategory.NETWORK_UNREACHABLE,
            ToolFailureCategory.NETWORK_TIMEOUT,
        ):
            raise ValueError(
                "ToolNetworkError category must be network_unreachable or network_timeout"
            )
        super().__init__(detail, category=net_category, retryable=retryable)


class ToolValidationError(ToolFailureError):
    category = ToolFailureCategory.INVALID_ARGUMENTS
    retryable = False


class ToolNotFoundError(ToolFailureError):
    category = ToolFailureCategory.TOOL_NOT_FOUND
    retryable = False


class ToolPermissionError(ToolFailureError):
    category = ToolFailureCategory.PERMISSION_DENIED
    retryable = False


class ToolCancelledError(ToolFailureError):
    category = ToolFailureCategory.CANCELLED
    retryable = False


def raise_tool_failure(
    category: ToolFailureCategory,
    detail: str,
    *,
    retryable: bool | None = None,
) -> NoReturn:
    """按 category 抛出对应 ToolFailureError 子类。"""
    mapping: dict[ToolFailureCategory, type[ToolFailureError]] = {
        ToolFailureCategory.INFRASTRUCTURE: ToolInfrastructureError,
        ToolFailureCategory.EXECUTION_TIMEOUT: ToolTimeoutError,
        ToolFailureCategory.NETWORK_UNREACHABLE: ToolNetworkError,
        ToolFailureCategory.NETWORK_TIMEOUT: ToolNetworkError,
        ToolFailureCategory.INVALID_ARGUMENTS: ToolValidationError,
        ToolFailureCategory.TOOL_NOT_FOUND: ToolNotFoundError,
        ToolFailureCategory.PERMISSION_DENIED: ToolPermissionError,
        ToolFailureCategory.CANCELLED: ToolCancelledError,
    }
    cls = mapping.get(category, ToolFailureError)
    if cls is ToolNetworkError:
        raise ToolNetworkError(detail, category=category, retryable=retryable)
    if cls is ToolFailureError:
        raise ToolFailureError(
            detail,
            category=category,
            retryable=retryable if retryable is not None else False,
        )
    raise cls(detail, retryable=retryable)  # type: ignore[misc]
