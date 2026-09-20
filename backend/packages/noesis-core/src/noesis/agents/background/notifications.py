"""后台子 Agent 终态通知（会话级待送达队列）。

任务到达终态时写入；该会话下一次 run 组装输入前 drain，以
``[系统通知]`` 前缀注入 agent_query（注入文本不落库，只注入一次）。

持久化（Phase 2）：record 异步落库（``bg_task_notifications`` 表，fire-and-
forget 经主 loop），take_undelivered 送达后删行——表内只存未送达。进程
重启后由启动恢复（services.bg_notification_store.restore_undelivered_
notifications）把未送达行装载回内存（load_persisted），通知链跨重启存活。
内存与 DB 的窗口不一致可接受：落库失败降级为 Phase 2 之前的纯内存行为；
送达后删行失败最多导致重启后重复注入一次（at-least-once）。
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

from noesis.chat.event_mapping.retrieval import (
    MAX_CROSS_BOUNDARY_SOURCES,
    format_sources_appendix,
)
from noesis.runtime.logging import logger

PREVIEW_MAX_CHARS = 80

_LOCK = threading.Lock()
_PENDING: dict[str, list[dict[str, Any]]] = {}
# 本进程已送达通知的墓碑（notice id）：删行是 fire-and-forget，DB 行消失
# 前若发生恢复装载（启动恢复/测试），墓碑阻止已送达通知重复注入。
# 超上限整会话清空（病态量级下退回 at-least-once，防内存无界）
_DELIVERED_IDS: dict[str, set[str]] = {}
_DELIVERED_IDS_MAX = 512


def _dispatch_persist(coro_factory, name: str) -> None:
    """fire-and-forget 投递到主 loop；无主 loop（单测）静默跳过。"""
    from noesis.runtime.main_loop import run_on_main_loop

    run_on_main_loop(coro_factory(), name=name)


def record(
    session_id: str,
    task_id: str,
    status: str,
    preview: str | None,
    label: str | None = None,
    step_count: int | None = None,
    duration_ms: int | None = None,
    turn_count: int | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> None:
    """记录一条终态通知（executor 终态转换点调用）：内存 + 异步落库。

    sources 为该子会话的去重来源清单（结构化字段，不混入预览文本）；
    无来源不写空清单占位。
    """
    if not session_id:
        return
    trimmed = (preview or "").strip()
    if len(trimmed) > PREVIEW_MAX_CHARS:
        trimmed = f"{trimmed[:PREVIEW_MAX_CHARS]}…"
    notice: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "task_id": task_id,
        "label": (label or "").strip()[:80],
        "status": status,
        "preview": trimmed,
        "step_count": step_count,
        "duration_ms": duration_ms,
        "turn_count": turn_count,
        "delivered": False,
    }
    clean_sources = [s for s in (sources or []) if isinstance(s, dict)]
    if clean_sources:
        notice["sources"] = clean_sources[:MAX_CROSS_BOUNDARY_SOURCES]
    with _LOCK:
        _PENDING.setdefault(session_id, []).append(notice)

    async def _persist() -> None:
        from noesis.agents.background.ports import NotificationStorePort

        payload = {k: v for k, v in notice.items() if k != "delivered"}
        try:
            await NotificationStorePort.persist(session_id, payload)
        except Exception:  # noqa: BLE001
            logger.warning("bg 通知落库失败 session_id={} task_id={}", session_id, task_id)

    _dispatch_persist(_persist, f"bg-notice-persist:{session_id}")


def take_undelivered(session_id: str, *, mark_delivered: bool = True) -> list[dict[str, Any]]:
    """取未送达通知（run 内中间件 / 下一轮注入共用；默认同时标记已送达）。"""
    with _LOCK:
        pending = _PENDING.get(session_id) or []
        undelivered = [dict(n) for n in pending if not n.get("delivered")]
        delivered_ids = [n["id"] for n in undelivered if n.get("id")]
        if mark_delivered and undelivered:
            for notice in pending:
                if not notice.get("delivered"):
                    notice["delivered"] = True
            tombstones = _DELIVERED_IDS.setdefault(session_id, set())
            tombstones.update(delivered_ids)
            if len(tombstones) > _DELIVERED_IDS_MAX:
                _DELIVERED_IDS.pop(session_id, None)
            if all(n.get("delivered") for n in pending):
                _PENDING.pop(session_id, None)
    if mark_delivered and delivered_ids:
        async def _delete() -> None:
            from noesis.agents.background.ports import NotificationStorePort

            try:
                await NotificationStorePort.delete_delivered(session_id, delivered_ids)
            except Exception:  # noqa: BLE001
                logger.warning("bg 通知送达删行失败 session_id={} count={}", session_id, len(delivered_ids))

        _dispatch_persist(_delete, f"bg-notice-delivered:{session_id}")
    return undelivered


def load_persisted(rows: list[tuple[str, dict[str, Any]]]) -> int:
    """启动恢复：DB 未送达行装载回内存（进程重启后内存为空时调用）。

    rows 为 (session_id, payload) 列表，按 created_at 升序传入保证注入顺序。
    幂等：已在内存的通知（同 id）跳过，重复恢复不产生重复注入。
    返回实际装载数。
    """
    loaded = 0
    with _LOCK:
        for session_id, payload in rows:
            known_ids = {n.get("id") for n in _PENDING.get(session_id, [])}
            known_ids |= _DELIVERED_IDS.get(session_id, set())
            if payload.get("id") in known_ids:
                continue
            _PENDING.setdefault(session_id, []).append({**payload, "delivered": False})
            loaded += 1
    return loaded


def drain(session_id: str) -> list[dict[str, Any]]:
    """取出并清空该会话的全部通知（兼容旧测试入口）。"""
    with _LOCK:
        _DELIVERED_IDS.pop(session_id, None)
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
            # 取消携带部分产出（协作停止的成果回收）：与 check_async_task / task.result 同源
            suffix = f"{metric_suffix}：{preview}" if preview else metric_suffix
            lines.append(f"[系统通知] 子 Agent「{label}」已取消{suffix}。")
        else:
            lines.append(f"[系统通知] 子 Agent「{label}」状态更新：{status}。")
        sources = notice.get("sources")
        if isinstance(sources, list) and sources:
            appendix = format_sources_appendix(sources)
            if appendix:
                lines.append(appendix)
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


__all__ = [
    "PREVIEW_MAX_CHARS",
    "drain",
    "load_persisted",
    "notify_agent_query",
    "record",
    "render_block",
    "take_undelivered",
]
