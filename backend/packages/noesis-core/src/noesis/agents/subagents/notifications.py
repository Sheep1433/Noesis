"""后台子 Agent 终态通知（会话级待送达队列）。

任务到达终态时写入；该会话下一次 run 组装输入前 drain，以
``[系统通知]`` 前缀注入 agent_query（不落库，只注入一次）。
队列在内存，与任务注册表同生命周期（进程重启同丢，一致）。
"""

from __future__ import annotations

import threading
from typing import Any

from noesis.runtime.logging import logger

PREVIEW_MAX_CHARS = 80

_LOCK = threading.Lock()
_PENDING: dict[str, list[dict[str, Any]]] = {}


def record(session_id: str, task_id: str, status: str, preview: str | None) -> None:
    """记录一条终态通知（executor 终态转换点调用）。"""
    if not session_id:
        return
    trimmed = (preview or "").strip()
    if len(trimmed) > PREVIEW_MAX_CHARS:
        trimmed = f"{trimmed[:PREVIEW_MAX_CHARS]}…"
    with _LOCK:
        _PENDING.setdefault(session_id, []).append({
            "task_id": task_id,
            "status": status,
            "preview": trimmed,
        })


def drain(session_id: str) -> list[dict[str, Any]]:
    """取出并清空该会话的待送达通知（run 启动时消费，一次性）。"""
    with _LOCK:
        return _PENDING.pop(session_id, [])


def render_block(notices: list[dict[str, Any]]) -> str:
    """把通知列表渲染成注入 agent_query 的系统通知块。"""
    lines: list[str] = []
    for notice in notices:
        status = str(notice.get("status") or "")
        task_id = str(notice.get("task_id") or "")
        preview = str(notice.get("preview") or "")
        if status == "completed":
            suffix = f"（结果预览：{preview}）" if preview else ""
            lines.append(
                f"[系统通知] 后台任务 {task_id} 已完成{suffix}，可用 check_task 收取完整结果。"
            )
        elif status in {"failed", "timed_out"}:
            suffix = f"：{preview}" if preview else ""
            lines.append(f"[系统通知] 后台任务 {task_id} {status}{suffix}。可 list_tasks 查看或重新委派。")
        elif status == "cancelled":
            lines.append(f"[系统通知] 后台任务 {task_id} 已取消。")
        else:
            lines.append(f"[系统通知] 后台任务 {task_id} 状态更新：{status}。")
    return "\n".join(lines)


def notify_agent_query(session_id: str, agent_query: str) -> str:
    """drain 会话通知并前置到 agent_query（无通知时原样返回）。"""
    notices = drain(session_id)
    if not notices:
        return agent_query
    block = render_block(notices)
    logger.info(
        "bg task notifications injected session_id={} count={}",
        session_id, len(notices),
    )
    return f"{block}\n\n{agent_query}".strip() if agent_query else block


__all__ = ["PREVIEW_MAX_CHARS", "drain", "notify_agent_query", "record", "render_block"]
