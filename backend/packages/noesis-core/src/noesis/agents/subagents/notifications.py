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


def record(
    session_id: str,
    task_id: str,
    status: str,
    preview: str | None,
    label: str | None = None,
    step_count: int | None = None,
    duration_ms: int | None = None,
    turn_count: int | None = None,
) -> None:
    """记录一条终态通知（executor 终态转换点调用）。"""
    if not session_id:
        return
    trimmed = (preview or "").strip()
    if len(trimmed) > PREVIEW_MAX_CHARS:
        trimmed = f"{trimmed[:PREVIEW_MAX_CHARS]}…"
    with _LOCK:
        _PENDING.setdefault(session_id, []).append({
            "task_id": task_id,
            "label": (label or "").strip()[:80],
            "status": status,
            "preview": trimmed,
            "step_count": step_count,
            "duration_ms": duration_ms,
            "turn_count": turn_count,
            "delivered": False,
        })


def take_undelivered(session_id: str, *, mark_delivered: bool = True) -> list[dict[str, Any]]:
    """取未送达通知（run 内中间件 / 下一轮注入共用；默认同时标记已送达）。"""
    with _LOCK:
        pending = _PENDING.get(session_id) or []
        undelivered = [dict(n) for n in pending if not n.get("delivered")]
        if mark_delivered and undelivered:
            for notice in pending:
                if not notice.get("delivered"):
                    notice["delivered"] = True
            if all(n.get("delivered") for n in pending):
                _PENDING.pop(session_id, None)
        return undelivered


def drain(session_id: str) -> list[dict[str, Any]]:
    """取出并清空该会话的全部通知（兼容旧测试入口）。"""
    with _LOCK:
        return _PENDING.pop(session_id, [])


def render_block(notices: list[dict[str, Any]]) -> str:
    """把通知列表渲染成注入 agent_query 的系统通知块。"""
    lines: list[str] = []
    for notice in notices:
        status = str(notice.get("status") or "")
        label = str(notice.get("label") or "").strip() or "子 Agent"
        preview = str(notice.get("preview") or "")
        metrics: list[str] = []
        if notice.get("turn_count") is not None:
            metrics.append(f"{int(notice['turn_count'])} 轮")
        if notice.get("step_count") is not None:
            metrics.append(f"{int(notice['step_count'])} 步")
        if notice.get("duration_ms") is not None:
            duration = max(0, int(notice["duration_ms"]))
            if duration < 1000:
                metrics.append("<1s")
            elif duration < 60_000:
                metrics.append(f"{duration // 1000}s")
            else:
                metrics.append(f"{duration // 60_000}m {duration // 1000 % 60:02d}s")
        metric_suffix = f" · {' · '.join(metrics)}" if metrics else ""
        if status == "completed":
            suffix = f"{metric_suffix}（结果预览：{preview}）" if preview else metric_suffix
            lines.append(f"[系统通知] 子 Agent「{label}」已完成{suffix}，可打开详情查看完整过程。")
        elif status in {"failed", "timed_out"}:
            suffix = f"{metric_suffix}：{preview}" if preview else metric_suffix
            title = "执行超时" if status == "timed_out" else "执行失败"
            lines.append(f"[系统通知] 子 Agent「{label}」{title}{suffix}，可打开详情查看原因。")
        elif status == "cancelled":
            # 取消携带部分产出（协作停止的成果回收）：与 check_task / task.result 同源
            suffix = f"{metric_suffix}：{preview}" if preview else metric_suffix
            lines.append(f"[系统通知] 子 Agent「{label}」已取消{suffix}。")
        else:
            lines.append(f"[系统通知] 子 Agent「{label}」状态更新：{status}。")
    return "\n".join(lines)


def notify_agent_query(session_id: str, agent_query: str) -> str:
    """取未送达通知并前置到 agent_query（无通知时原样返回）。"""
    notices = take_undelivered(session_id)
    if not notices:
        return agent_query
    block = render_block(notices)
    logger.info(
        "bg task notifications injected session_id={} count={}",
        session_id, len(notices),
    )
    return f"{block}\n\n{agent_query}".strip() if agent_query else block


__all__ = ["PREVIEW_MAX_CHARS", "drain", "notify_agent_query", "record", "render_block", "take_undelivered"]
