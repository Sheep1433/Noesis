"""工具失败统一分类、LLM/用户双通道文案与 SSE 字段映射。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

TOOL_ERROR_PREFIX = "[tool_error"
_TOOL_ERROR_HEADER_RE = re.compile(
    r"^\[tool_error\s+category=([a-z_]+)(?:\s+retryable=(true|false))?\]\s*",
    re.I,
)

# ---------- 基础设施标记（failure_notice 共用）----------

INTERNAL_INFRASTRUCTURE_MARKERS = (
    re.compile(r"\[INTERNAL_ERROR\]", re.I),
    re.compile(r"docker image .+ not found", re.I),
    re.compile(r"\bdocker pull\b", re.I),
    re.compile(r"sandbox.*not (?:ready|available)", re.I),
)

_NETWORK_UNREACHABLE_MARKERS = re.compile(
    r"connection refused|econnrefused|network is unreachable|"
    r"host is unreachable|no route to host|name or service not known|"
    r"无法连接|主机不可达|连接被拒绝",
    re.I,
)

_NETWORK_TIMEOUT_MARKERS = re.compile(
    r"connecttimeout|connection timed out|timed out while connecting|"
    r"socket hang up|readtimeout.*connect|connect error.*timeout|"
    r"连接超时",
    re.I,
)

_EXECUTION_TIMEOUT_MARKERS = re.compile(
    r"\bTimeoutError\b|execution timed out|command timed out|"
    r"tool execution timed out|asyncio\.timeouts|"
    r"执行超时|操作超时",
    re.I,
)

_INVALID_ARGUMENTS_MARKERS = re.compile(
    r"ValidationError|validation error|invalid argument|invalid params|"
    r"invalid parameter|pydantic|field required|type error.*argument|"
    r"参数错误|参数校验|非法参数",
    re.I,
)

_TOOL_NOT_FOUND_MARKERS = re.compile(
    r"tool not found|unknown tool|no tool named|is not a valid tool|"
    r"工具不存在|未注册的工具",
    re.I,
)

_PERMISSION_DENIED_MARKERS = re.compile(
    r"permission denied|access denied|\b403\b|forbidden|not authorized|"
    r"权限不足|拒绝访问",
    re.I,
)

_CANCELLED_MARKERS = re.compile(
    r"user stop|stop_chat|cancelled|canceled|用户已停止|已停止生成",
    re.I,
)

_SUBAGENT_FAILURE_MARKERS = re.compile(
    r"^Task failed\.|^Task timed out|\[tool_error\s+category=subagent_failure",
    re.I,
)

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


@dataclass(frozen=True)
class ToolFailure:
    category: ToolFailureCategory
    message_for_llm: str
    message_for_user: str
    retryable: bool


def is_infrastructure_failure(raw: str) -> bool:
    s = (raw or "").strip()
    if not s:
        return False
    return any(p.search(s) for p in INTERNAL_INFRASTRUCTURE_MARKERS)


def _user_message_for_category(category: ToolFailureCategory) -> str:
    return USER_TOOL_ERROR_MESSAGES.get(category.value, DEFAULT_USER_TOOL_ERROR)


def _extract_text(exc: BaseException | None, raw: str) -> str:
    parts: list[str] = []
    if exc is not None:
        parts.append(str(exc).strip())
        parts.append(exc.__class__.__name__)
    if raw:
        parts.append(raw.strip())
    return "\n".join(p for p in parts if p)


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


def _classify_from_text(text: str, *, tool_name: str = "") -> ToolFailureCategory:
    t = text.strip()
    if not t:
        return ToolFailureCategory.UNKNOWN

    parsed_cat, _, body = _parse_tool_error_header(t)
    if parsed_cat is not None:
        return parsed_cat

    if _SUBAGENT_FAILURE_MARKERS.search(t) or (
        tool_name == "task" and t.lower().startswith(("task failed.", "task timed out"))
    ):
        return ToolFailureCategory.SUBAGENT_FAILURE
    if is_infrastructure_failure(t):
        return ToolFailureCategory.INFRASTRUCTURE
    if _CANCELLED_MARKERS.search(t):
        return ToolFailureCategory.CANCELLED
    if _INVALID_ARGUMENTS_MARKERS.search(t):
        return ToolFailureCategory.INVALID_ARGUMENTS
    if _TOOL_NOT_FOUND_MARKERS.search(t):
        return ToolFailureCategory.TOOL_NOT_FOUND
    if _PERMISSION_DENIED_MARKERS.search(t):
        return ToolFailureCategory.PERMISSION_DENIED
    if _NETWORK_UNREACHABLE_MARKERS.search(t):
        return ToolFailureCategory.NETWORK_UNREACHABLE
    if _NETWORK_TIMEOUT_MARKERS.search(t):
        return ToolFailureCategory.NETWORK_TIMEOUT
    if _EXECUTION_TIMEOUT_MARKERS.search(t):
        return ToolFailureCategory.EXECUTION_TIMEOUT
    if "timed out" in t.lower() or "timeout" in t.lower():
        if "connect" in t.lower() or "connection" in t.lower():
            return ToolFailureCategory.NETWORK_TIMEOUT
        return ToolFailureCategory.EXECUTION_TIMEOUT

    return ToolFailureCategory.UNKNOWN


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


def classify_tool_failure(
    exc: BaseException | None,
    *,
    raw: str = "",
    tool_name: str = "",
) -> ToolFailure:
    """单一分类入口：异常和/或原始文本 → ToolFailure。"""
    if exc is not None:
        exc_name = exc.__class__.__name__
        if exc_name == "ValidationError":
            category = ToolFailureCategory.INVALID_ARGUMENTS
            text = _extract_text(exc, raw)
            retryable = False
            return ToolFailure(
                category=category,
                message_for_llm=_build_llm_message(
                    category,
                    tool_name=tool_name or "unknown_tool",
                    detail=text,
                    retryable=retryable,
                ),
                message_for_user=_user_message_for_category(category),
                retryable=retryable,
            )
        if exc_name in ("TimeoutError", "ReadTimeout", "WriteTimeout", "ConnectTimeout"):
            category = ToolFailureCategory.EXECUTION_TIMEOUT
            if "connect" in str(exc).lower():
                category = ToolFailureCategory.NETWORK_TIMEOUT
            text = _extract_text(exc, raw)
            retryable = category.value in _RETRYABLE_CATEGORIES
            return ToolFailure(
                category=category,
                message_for_llm=_build_llm_message(
                    category,
                    tool_name=tool_name or "unknown_tool",
                    detail=text,
                    retryable=retryable,
                ),
                message_for_user=_user_message_for_category(category),
                retryable=retryable,
            )
        if exc_name in ("CancelledError", "KeyboardInterrupt"):
            category = ToolFailureCategory.CANCELLED
            text = _extract_text(exc, raw)
            return ToolFailure(
                category=category,
                message_for_llm=_build_llm_message(
                    category,
                    tool_name=tool_name or "unknown_tool",
                    detail=text,
                    retryable=False,
                ),
                message_for_user=_user_message_for_category(category),
                retryable=False,
            )

    text = _extract_text(exc, raw)
    parsed_cat, parsed_retry, body = _parse_tool_error_header(text)
    if parsed_cat is not None:
        category = parsed_cat
        detail = body or text
        retryable = (
            parsed_retry
            if parsed_retry is not None
            else category.value in _RETRYABLE_CATEGORIES
        )
    else:
        category = _classify_from_text(text, tool_name=tool_name)
        detail = text
        retryable = category.value in _RETRYABLE_CATEGORIES

    name = tool_name or "unknown_tool"
    message_for_llm = _build_llm_message(
        category,
        tool_name=name,
        detail=detail,
        retryable=retryable,
    )
    return ToolFailure(
        category=category,
        message_for_llm=message_for_llm,
        message_for_user=_user_message_for_category(category),
        retryable=retryable,
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
