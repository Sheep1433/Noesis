"""工具失败：异常层次、统一分类、LLM/用户双通道文案与 SSE 字段映射。"""

from __future__ import annotations

import asyncio
import errno
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, NoReturn, Optional

import httpx
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from pydantic import ValidationError


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

TOOL_ERROR_PREFIX = "[tool_error"
_TOOL_ERROR_HEADER_RE = re.compile(
    r"^\[tool_error\s+category=([a-z_]+)(?:\s+retryable=(true|false))?\]\s*",
    re.I,
)

_DETAIL_MAX_LEN = 10_000

USER_TOOL_ERROR_MESSAGES: dict[str, str] = {
    "network_unreachable": "连接失败",
    "network_timeout": "连接失败",
    "execution_timeout": "执行超时",
    "invalid_arguments": "参数错误",
    "infrastructure": "环境不可用",
    "cancelled": "已停止",
}
DEFAULT_USER_TOOL_ERROR = "执行失败"

_RETRYABLE_CATEGORIES = frozenset({
    "network_unreachable",
    "network_timeout",
    "execution_timeout",
    "infrastructure",
})

_NETWORK_ERRNOS = frozenset({
    errno.ECONNREFUSED,
    errno.ENETUNREACH,
    errno.EHOSTUNREACH,
    errno.ENETDOWN,
})


@dataclass(frozen=True)
class ToolFailure:
    category: ToolFailureCategory
    message_for_llm: str
    message_for_user: str
    retryable: bool


def _user_message_for_category(category: ToolFailureCategory) -> str:
    return USER_TOOL_ERROR_MESSAGES.get(category.value, DEFAULT_USER_TOOL_ERROR)


def _default_retryable(category: ToolFailureCategory) -> bool:
    return category.value in _RETRYABLE_CATEGORIES


def format_tool_error_detail(exc: BaseException | None, raw: str = "") -> str:
    """合并异常与 raw 文本，截断至 10k，附带类型名。"""
    parts: list[str] = []
    if exc is not None:
        exc_text = str(exc).strip()
        if exc_text:
            parts.append(exc_text)
        parts.append(exc.__class__.__name__)
    raw_text = (raw or "").strip()
    if raw_text:
        parts.append(raw_text)
    detail = "\n".join(p for p in parts if p)
    if len(detail) > _DETAIL_MAX_LEN:
        return detail[:_DETAIL_MAX_LEN] + "…"
    return detail


def _parse_tool_error_header(raw: str) -> tuple[Optional[ToolFailureCategory], Optional[bool], str]:
    s = (raw or "").strip()
    m = _TOOL_ERROR_HEADER_RE.match(s)
    if not m:
        return None, None, s
    cat_str = m.group(1).lower()
    retry_raw = m.group(2)
    retryable = retry_raw.lower() == "true" if retry_raw else None
    try:
        category = ToolFailureCategory(cat_str)
    except ValueError:
        category = ToolFailureCategory.UNKNOWN
    remainder = s[m.end() :].strip()
    return category, retryable, remainder


def _iter_exception_chain(exc: BaseException, *, max_depth: int = 2):
    """深度受限遍历 __cause__ / __context__，去重。"""
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    depth = 0
    while stack and depth < max_depth:
        current = stack.pop(0)
        node_id = id(current)
        if node_id in seen:
            continue
        seen.add(node_id)
        yield current
        depth += 1
        cause = current.__cause__
        context = current.__context__
        if cause is not None and id(cause) not in seen:
            stack.append(cause)
        if (
            context is not None
            and id(context) not in seen
            and context is not cause
        ):
            stack.append(context)


def _map_exception(node: BaseException) -> tuple[ToolFailureCategory, bool] | None:
    """仅按 type(node) 与 errno 映射，不解析 str(exc)。"""
    if isinstance(node, ToolFailureError):
        return node.category, node.retryable
    if isinstance(node, ValidationError) or isinstance(node, ToolValidationError):
        return ToolFailureCategory.INVALID_ARGUMENTS, False
    if isinstance(node, ToolInfrastructureError):
        return ToolFailureCategory.INFRASTRUCTURE, True
    if isinstance(node, (asyncio.TimeoutError, TimeoutError)):
        return ToolFailureCategory.EXECUTION_TIMEOUT, True
    if isinstance(node, httpx.ConnectTimeout):
        return ToolFailureCategory.NETWORK_TIMEOUT, True
    if isinstance(node, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
        return ToolFailureCategory.EXECUTION_TIMEOUT, True
    if isinstance(node, (httpx.ConnectError, ConnectionRefusedError)):
        return ToolFailureCategory.NETWORK_UNREACHABLE, True
    if isinstance(node, PermissionError):
        return ToolFailureCategory.PERMISSION_DENIED, False
    if isinstance(node, (asyncio.CancelledError, KeyboardInterrupt, ToolCancelledError)):
        return ToolFailureCategory.CANCELLED, False
    if isinstance(node, OSError):
        err = node.errno
        if err in _NETWORK_ERRNOS:
            return ToolFailureCategory.NETWORK_UNREACHABLE, True
        if err == errno.ETIMEDOUT:
            return ToolFailureCategory.NETWORK_TIMEOUT, True
        if err in (errno.EACCES, errno.EPERM):
            return ToolFailureCategory.PERMISSION_DENIED, False
    if isinstance(node, ConnectionError) and not isinstance(node, OSError):
        return ToolFailureCategory.NETWORK_UNREACHABLE, True
    return None


def _classify_from_exception_chain(
    exc: BaseException,
) -> tuple[ToolFailureCategory, bool] | None:
    for node in _iter_exception_chain(exc):
        mapped = _map_exception(node)
        if mapped is not None:
            return mapped
    return None


def _classify_task_prefix(raw: str, tool_name: str) -> ToolFailureCategory | None:
    if tool_name != "task":
        return None
    t = (raw or "").strip()
    if t.startswith("Task failed.") or t.startswith("Task timed out"):
        return ToolFailureCategory.SUBAGENT_FAILURE
    return None


def _suggestion_for_category(category: ToolFailureCategory, tool_name: str) -> str:
    suggestions = {
        ToolFailureCategory.NETWORK_UNREACHABLE: (
            "check host reachability, endpoint URL, or MCP server availability."
        ),
        ToolFailureCategory.NETWORK_TIMEOUT: (
            "retry with a simpler request or verify network connectivity."
        ),
        ToolFailureCategory.EXECUTION_TIMEOUT: (
            "retry with a smaller scope, shorter command, or narrower query."
        ),
        ToolFailureCategory.INVALID_ARGUMENTS: (
            "fix tool arguments according to the schema and retry."
        ),
        ToolFailureCategory.TOOL_NOT_FOUND: (
            "use a registered tool name or adjust the plan."
        ),
        ToolFailureCategory.PERMISSION_DENIED: (
            "verify credentials, paths, or access scope before retrying."
        ),
        ToolFailureCategory.INFRASTRUCTURE: (
            "wait for the environment or ask the operator to check MCP/sandbox."
        ),
        ToolFailureCategory.SUBAGENT_FAILURE: (
            "review the sub-task failure and adjust the delegation prompt."
        ),
        ToolFailureCategory.CANCELLED: (
            "the run was stopped; do not retry unless the user asks again."
        ),
        ToolFailureCategory.UNKNOWN: (
            "continue with available context or choose an alternative tool."
        ),
    }
    return suggestions.get(category, suggestions[ToolFailureCategory.UNKNOWN])


def _build_llm_message(
    category: ToolFailureCategory,
    *,
    tool_name: str,
    detail: str,
    retryable: bool,
) -> str:
    detail_line = detail.strip() or category.value
    header = f"[tool_error category={category.value} retryable={'true' if retryable else 'false'}]"
    suggestion = _suggestion_for_category(category, tool_name)
    return (
        f"{header}\n"
        f"Tool '{tool_name}' failed: {detail_line}\n"
        f"Suggestion: {suggestion}"
    )


def _failure_from_parts(
    category: ToolFailureCategory,
    *,
    tool_name: str,
    detail: str,
    retryable: bool,
) -> ToolFailure:
    name = tool_name or "unknown_tool"
    return ToolFailure(
        category=category,
        message_for_llm=_build_llm_message(
            category,
            tool_name=name,
            detail=detail,
            retryable=retryable,
        ),
        message_for_user=_user_message_for_category(category),
        retryable=retryable,
    )


def classify_tool_failure(
    exc: BaseException | None,
    *,
    raw: str = "",
    tool_name: str = "",
) -> ToolFailure:
    """单一分类入口：异常和/或原始文本 → ToolFailure（类型优先，禁止文本正则推断）。"""
    name = tool_name or "unknown_tool"

    if isinstance(exc, ToolFailureError):
        return _failure_from_parts(
            exc.category,
            tool_name=name,
            detail=exc.detail or format_tool_error_detail(exc, raw),
            retryable=exc.retryable,
        )

    chain_result: tuple[ToolFailureCategory, bool] | None = None
    if exc is not None:
        chain_result = _classify_from_exception_chain(exc)

    parsed_cat, parsed_retry, body = _parse_tool_error_header(raw)
    task_prefix = _classify_task_prefix(raw, tool_name)

    if chain_result is not None:
        category, retryable = chain_result
        detail = format_tool_error_detail(exc, raw)
        return _failure_from_parts(
            category,
            tool_name=name,
            detail=detail,
            retryable=retryable,
        )

    if parsed_cat is not None:
        category = parsed_cat
        detail = body or format_tool_error_detail(exc, raw)
        retryable = (
            parsed_retry
            if parsed_retry is not None
            else _default_retryable(category)
        )
        return _failure_from_parts(
            category,
            tool_name=name,
            detail=detail,
            retryable=retryable,
        )

    if task_prefix is not None:
        return _failure_from_parts(
            task_prefix,
            tool_name=name,
            detail=format_tool_error_detail(exc, raw),
            retryable=False,
        )

    detail = format_tool_error_detail(exc, raw)
    return _failure_from_parts(
        ToolFailureCategory.UNKNOWN,
        tool_name=name,
        detail=detail,
        retryable=False,
    )


def build_error_tool_message(request: ToolCallRequest, failure: ToolFailure) -> ToolMessage:
    tool_name = str(request.tool_call.get("name") or "unknown_tool")
    tool_call_id = str(request.tool_call.get("id") or "missing_tool_call_id")
    return ToolMessage(
        content=failure.message_for_llm,
        tool_call_id=tool_call_id,
        name=tool_name,
        status="error",
    )


def failure_to_sse_error_fields(failure: ToolFailure) -> dict[str, Any]:
    return {
        "error": failure.message_for_user,
        "errorCategory": failure.category.value,
    }


def classify_task_tool_output(raw_output: str) -> Optional[ToolFailure]:
    """解析 task 工具包装文本；成功返回 None，失败返回 ToolFailure。"""
    trimmed = (raw_output or "").strip()
    if not trimmed:
        return None
    if trimmed.startswith("Task Succeeded. Result:"):
        return None
    if trimmed.startswith("Task failed.") or trimmed.startswith("Task timed out"):
        return classify_tool_failure(None, raw=trimmed, tool_name="task")
    return None


def subagent_failure_from_context(detail: str = "") -> ToolFailure:
    """子图含 error tool 时构造 subagent_failure。"""
    header = "[tool_error category=subagent_failure retryable=false]"
    raw = f"{header}\n{detail}".strip() if detail else header
    return classify_tool_failure(None, raw=raw, tool_name="task")
