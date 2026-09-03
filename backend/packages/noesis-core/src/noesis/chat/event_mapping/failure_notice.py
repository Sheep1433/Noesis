"""流式错误展示与落库：脱敏、失败说明文案（与前端 messageParts 语义对齐）。"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional

from noesis.errors.tool_failure import (
    DEFAULT_USER_TOOL_ERROR,
    classify_tool_failure,
)

# ---------- 用户可见错误脱敏（SSE / 中间件 / 落库共用）----------


def strip_tool_error_prefix(raw: str) -> str:
    s = (raw or "").strip()
    if s.lower().startswith("tool error:"):
        return s[11:].strip()
    return s


def is_internal_infrastructure_error(raw: str) -> bool:
    """整轮流错误路径：仅 [INTERNAL_ERROR] 前缀检测，不用宽泛正则。"""
    s = strip_tool_error_prefix(raw).strip()
    return s.startswith("[INTERNAL_ERROR]")


def sanitize_tool_error(raw: str, *, default: str = DEFAULT_USER_TOOL_ERROR) -> str:
    """单 tool 失败兜底：委托 classify（UNKNOWN 纯文本固定为「执行失败」）。"""
    s = strip_tool_error_prefix(raw)
    if not s:
        return default
    return classify_tool_failure(None, raw=s).text


def sanitize_stream_error(raw: str, *, default: str = "操作失败，请稍后重试。") -> str:
    """整轮 SSE error 事件：独立规则，不委托 tool 文本分类器。"""
    s = strip_tool_error_prefix(raw)
    if not s:
        return default
    if is_internal_infrastructure_error(s):
        return "环境不可用"
    if is_recursion_limit_error(s):
        return (
            "已达到最大处理步数，任务已停止。请精简问题后重试。"
        )
    if is_model_api_timeout_error(s):
        return "模型响应超时，请稍后重试。"
    if s == "HITL 已超时":
        return "等待确认已超时"
    lowered = s.lower()
    if any(marker in lowered for marker in (
        "authenticationerror",
        "invalid_api_key",
        "incorrect api key",
        "unauthorized",
        "status code: 401",
        "error code: 401",
    )):
        return "模型服务暂时不可用，请稍后重试。"
    if is_connection_or_timeout_error(s):
        return "连接失败，请稍后重试。"
    # 整轮错误来自服务端异常；未知原文只写日志，不回显路径、堆栈或 provider 细节。
    return default


# ---------- 失败说明文案 ----------


def is_recursion_limit_error(raw: str) -> bool:
    t = raw.strip().lower()
    return bool(
        re.search(
            r"recursion limit|graphrecursionerror|recursion_limit|已达到最大处理步数",
            t,
        )
    )


_MODEL_API_TIMEOUT_MARKERS = re.compile(
    r"readtimeout|writetimeout|connecttimeout|pooltimeout|"
    r"streamchunktimeouterror|stream_chunk_timeout|"
    r"apitimeout|request timed out|timed out waiting",
    re.I,
)

_NETWORK_TIMEOUT_MARKERS = re.compile(
    r"request timed out|timed out|\btimeout\b|apitimeout|connecterror|"
    r"connection refused|econnrefused|network is unreachable|socket hang up|"
    r"remoteprotocolerror|peer closed connection|incomplete chunked read|"
    r"无法连接|网络异常|网络错误|网络或服务异常",
    re.I,
)


def is_model_api_timeout_error(raw: str) -> bool:
    """上游 LLM HTTP 流式读超时（如 httpx.ReadTimeout），与浏览器网络错误区分。"""
    t = raw.strip()
    if not t:
        return False
    return bool(_MODEL_API_TIMEOUT_MARKERS.search(t))


def get_model_api_timeout_notice_text(has_prose: bool) -> str:
    if has_prose:
        return (
            "（模型响应超时，后续内容未能继续生成。"
            "请稍后重试，或尝试精简问题、缩短对话上下文。）"
        )
    return "模型响应超时，请稍后重试。"


def is_connection_or_timeout_error(raw: str) -> bool:
    t = raw.strip().lower().replace(" ", " ")
    if not t:
        return True
    if is_model_api_timeout_error(raw):
        return True
    if re.match(
        r"^(?:connection error|failed to fetch|networkerror|network request failed|"
        r"load failed|fetch error|typeerror:\s*failed to fetch)$",
        re.sub(r"[.。…!！]+$", "", t).strip(),
    ):
        return True
    return bool(_NETWORK_TIMEOUT_MARKERS.search(t))


def _has_tool_error_part(parts: List[Dict[str, Any]]) -> bool:
    return any(p.get("type") == "tool" and p.get("status") == "error" for p in parts)


def get_stream_failure_notice_text(
    detail: Optional[str],
    has_prose: bool,
    parts: Optional[List[Dict[str, Any]]] = None,
) -> Optional[str]:
    original = (detail or "").strip()
    if is_model_api_timeout_error(original):
        return get_model_api_timeout_notice_text(has_prose)
    raw = sanitize_stream_error(original, default="")
    if is_model_api_timeout_error(raw):
        return get_model_api_timeout_notice_text(has_prose)
    if is_connection_or_timeout_error(raw):
        return None
    if is_recursion_limit_error(raw):
        return (
            "（已达到最大处理步数，后续内容未能继续生成。）"
            if has_prose
            else "已达到最大处理步数，任务已停止。请精简问题后重试。"
        )
    if parts and _has_tool_error_part(parts):
        return "（后续内容未能生成）" if has_prose else None
    if not raw:
        return None if has_prose else "生成失败，请稍后重试。"
    clipped = raw if len(raw) <= 160 else f"{raw[:160]}…"
    head = "生成过程中出现问题，请稍后重试。"
    if is_internal_infrastructure_error(detail or ""):
        clipped = sanitize_stream_error(detail or "")
    return f"（后续内容未能生成）\n\n{clipped}" if has_prose else f"{head}\n\n{clipped}"


def _has_prose(parts: List[Dict[str, Any]]) -> bool:
    for p in parts:
        if p.get("type") in ("text", "reasoning"):
            if str(p.get("content") or "").strip():
                return True
    return False


_STREAM_FAILURE_NOTICE_MARKERS = (
    "生成过程中出现问题",
    "后续内容未能生成",
    "后续内容未能继续生成",
    "已达到最大处理步数",
    "模型响应超时",
    "生成失败，请稍后重试",
)


def _content_has_stream_failure_notice(parts: List[Dict[str, Any]]) -> bool:
    for p in parts:
        if p.get("type") != "text":
            continue
        content = str(p.get("content") or "")
        if any(marker in content for marker in _STREAM_FAILURE_NOTICE_MARKERS):
            return True
    return False


USER_STOP_TOOL_ERROR = "用户已停止生成"
# 统一单一形态：斜体括号附注（无横线/无纯文本变体——双形态曾让用户误判为不一致 bug）
USER_STOP_NOTICE = "（本轮回复已被用户中断。）"


def _mark_running_tools_error(
    parts: List[Dict[str, Any]],
    *,
    error_message: str,
    state: str = "failed",
) -> None:
    for p in parts:
        if p.get("type") == "tool" and p.get("status") == "running":
            p["status"] = "error"
            p["state"] = state
            p["outcome"] = "cancelled" if state == "cancelled" else "unknown"
            if not p.get("error"):
                p["error"] = error_message


def append_user_stop_notice_to_content(
    content_dict: Dict[str, Any],
) -> Dict[str, Any]:
    """用户主动停止：running 工具标 error，追加可读中断说明。

    统一形态：斜体括号附注一段（有残文时经空行自然衔接；markdown 忽略
    段首空行，无残文时独立成段不影响渲染）。
    """
    parts: List[Dict[str, Any]] = list(content_dict.get("parts") or [])
    _mark_running_tools_error(
        parts,
        error_message=USER_STOP_TOOL_ERROR,
        state="cancelled",
    )
    parts.append(
        {
            "id": f"part-text-{uuid.uuid4().hex[:12]}",
            "type": "text",
            "content": f"\n\n*{USER_STOP_NOTICE}*",
            "status": "completed",
        }
    )

    return {"version": content_dict.get("version", 1), "parts": parts}


def append_disconnect_partial_content(
    content_dict: Dict[str, Any],
) -> Dict[str, Any]:
    """连接中断落库：仅将 running 工具标 error，不追加用户中断说明。"""
    parts: List[Dict[str, Any]] = list(content_dict.get("parts") or [])
    _mark_running_tools_error(parts, error_message="工具未返回结果")
    return {"version": content_dict.get("version", 1), "parts": parts}


def append_stream_failure_notice_to_content(
    content_dict: Dict[str, Any],
    detail: Optional[str],
) -> Dict[str, Any]:
    parts: List[Dict[str, Any]] = list(content_dict.get("parts") or [])
    if _content_has_stream_failure_notice(parts):
        _mark_running_tools_error(parts, error_message="工具未返回结果")
        return {"version": content_dict.get("version", 1), "parts": parts}
    _mark_running_tools_error(parts, error_message="工具未返回结果")
    notice = get_stream_failure_notice_text(detail, _has_prose(parts), parts)
    if notice is None:
        return content_dict

    if not _has_prose(parts):
        if not parts:
            parts = [
                {
                    "id": f"part-text-{uuid.uuid4().hex[:12]}",
                    "type": "text",
                    "content": notice,
                    "status": "completed",
                }
            ]
        else:
            parts.append(
                {
                    "id": f"part-text-{uuid.uuid4().hex[:12]}",
                    "type": "text",
                    "content": notice,
                    "status": "completed",
                }
            )
    else:
        tail = (
            f"\n\n---\n\n*{notice}*"
            if notice.startswith("（")
            else f"\n\n---\n\n*（后续内容未能生成，请稍后重试。）*\n\n{notice}"
        )
        parts.append(
            {
                "id": f"part-text-{uuid.uuid4().hex[:12]}",
                "type": "text",
                "content": tail,
                "status": "completed",
            }
        )

    return {"version": content_dict.get("version", 1), "parts": parts}
