"""流式失败说明：与前端 messageParts.appendStreamFailureNotice 语义对齐，供持久化落库。"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional


def is_recursion_limit_error(raw: str) -> bool:
    t = raw.strip().lower()
    return bool(
        re.search(
            r"recursion limit|graphrecursionerror|recursion_limit|已达到最大处理步数",
            t,
        )
    )


def is_connection_or_timeout_error(raw: str) -> bool:
    t = raw.strip().lower().replace(" ", " ")
    if not t:
        return True
    if re.match(
        r"^(?:connection error|failed to fetch|networkerror|network request failed|"
        r"load failed|fetch error|typeerror:\s*failed to fetch)$",
        re.sub(r"[.。…!！]+$", "", t).strip(),
    ):
        return True
    return bool(
        re.search(
            r"request timed out|timed out|\btimeout\b|apitimeout|connecterror|"
            r"connection refused|econnrefused|network is unreachable|socket hang up|"
            r"无法连接|网络异常|网络错误|网络或服务异常",
            t,
        )
    )


def get_stream_failure_notice_text(
    detail: Optional[str],
    has_prose: bool,
) -> Optional[str]:
    raw = (detail or "").strip()
    if is_connection_or_timeout_error(raw):
        return None
    if is_recursion_limit_error(raw):
        return (
            "（已达到最大处理步数，后续内容未能继续生成。）"
            if has_prose
            else "已达到最大处理步数，任务已停止。请精简问题后重试。"
        )
    if not raw:
        return None if has_prose else "生成失败，请稍后重试。"
    clipped = raw if len(raw) <= 160 else f"{raw[:160]}…"
    head = "生成过程中出现问题，请稍后重试。"
    return f"（后续内容未能生成）\n\n{clipped}" if has_prose else f"{head}\n\n{clipped}"


def _has_prose(parts: List[Dict[str, Any]]) -> bool:
    for p in parts:
        if p.get("type") in ("text", "reasoning"):
            if str(p.get("content") or "").strip():
                return True
    return False


def append_stream_failure_notice_to_content(
    content_dict: Dict[str, Any],
    detail: Optional[str],
) -> Dict[str, Any]:
    parts: List[Dict[str, Any]] = list(content_dict.get("parts") or [])
    notice = get_stream_failure_notice_text(detail, _has_prose(parts))
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
