"""后台子 Agent 任务快照的同步持久化 repository。

执行面（``BackgroundSubagentExecutor``）运行在隔离线程的独立事件循环，
状态转换可能发生在任意线程；因此本 repository 走
``pg_manager.get_sync_session()`` 的同步路径（对齐
``kb_collection_config_repository`` 的先例），不占用主事件循环。

职责边界：
- ``save_task_snapshot``：任务状态转换点 upsert 最新快照（executor 注入调用）；
- ``get/list``：进程重启后内存注册表 miss 时的 fallback 查询；
- ``reconcile_interrupted_tasks``：启动对账——上一进程遗留的非终态任务
  标记为 failed（对齐 deepagents 教程 ch6「async_tasks channel 跨重启可恢复」
  的诉求：任务元数据不随进程消失）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update

from noesis.runtime.logging import logger
from noesis.storage.postgres.manager import pg_manager
from noesis.storage.postgres.models.bg_task import TBackgroundTask

# 非终态（重启后必须对账掉）
_ACTIVE_STATUSES = ("running", "awaiting_approval")
_INTERRUPTED_ERROR = "后端进程重启，任务中断"


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _to_row_values(snapshot: dict[str, Any]) -> dict[str, Any]:
    now_ms = _now_ms()
    started_at = snapshot.get("started_at")
    completed_at = snapshot.get("completed_at")
    return {
        "task_id": str(snapshot["task_id"]),
        "session_id": str(snapshot["session_id"]),
        "user_id": str(snapshot.get("user_id") or ""),
        "description": str(snapshot.get("description") or ""),
        "kind": str(snapshot.get("kind") or "continuable"),
        "status": str(snapshot.get("status") or "running"),
        "result": snapshot.get("result"),
        "error": snapshot.get("error"),
        "started_at": int(float(started_at) * 1000) if started_at else now_ms,
        "completed_at": int(float(completed_at) * 1000) if completed_at else None,
        "updated_at": now_ms,
    }


def _to_snapshot(row: TBackgroundTask) -> dict[str, Any]:
    return {
        "task_id": row.task_id,
        "session_id": row.session_id,
        "user_id": row.user_id,
        "description": row.description,
        "kind": row.kind,
        "status": row.status,
        "result": row.result,
        "error": row.error,
        "interrupt": None,
        "started_at": (row.started_at / 1000) if row.started_at else 0.0,
        "completed_at": (row.completed_at / 1000) if row.completed_at else None,
        "progress": [],
    }


def save_task_snapshot(snapshot: dict[str, Any]) -> None:
    """任务状态转换点 upsert 快照；持久化失败只记日志，不影响任务执行。"""
    values = _to_row_values(snapshot)
    with pg_manager.get_sync_session() as db:
        existing = db.get(TBackgroundTask, values["task_id"])
        if existing is None:
            db.add(TBackgroundTask(**values))
        else:
            for key, value in values.items():
                if key != "task_id":
                    setattr(existing, key, value)
        db.commit()


def get_task_snapshot(task_id: str) -> dict[str, Any] | None:
    with pg_manager.get_sync_session() as db:
        row = db.get(TBackgroundTask, task_id)
        return _to_snapshot(row) if row is not None else None


def list_task_snapshots(session_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    stmt = (
        select(TBackgroundTask)
        .where(TBackgroundTask.session_id == session_id)
        .order_by(TBackgroundTask.started_at.desc())
        .limit(limit)
    )
    with pg_manager.get_sync_session() as db:
        rows = db.execute(stmt).scalars().all()
    return [_to_snapshot(row) for row in rows]


def reconcile_interrupted_tasks() -> int:
    """启动对账：上一进程遗留的 running/awaiting_approval → failed。

    返回对账条数。任务执行本身无法跨进程恢复（执行面在进程内隔离 loop），
    但快照落库保证 check/list 在重启后仍可查询到明确终态。
    """
    now_ms = _now_ms()
    with pg_manager.get_sync_session() as db:
        result = db.execute(
            update(TBackgroundTask)
            .where(TBackgroundTask.status.in_(_ACTIVE_STATUSES))
            .values(
                status="failed",
                error=_INTERRUPTED_ERROR,
                completed_at=now_ms,
                updated_at=now_ms,
            )
        )
        count = int(result.rowcount or 0)
        db.commit()
    if count:
        logger.warning("bg task reconcile: {} interrupted tasks marked failed", count)
    return count


class BgTaskRepositoryAdapter:
    """``BgTaskStore`` 协议实现（executor 注入用）：模块函数的薄包装。"""

    def save(self, snapshot: dict[str, Any]) -> None:
        save_task_snapshot(snapshot)

    def get(self, task_id: str) -> dict[str, Any] | None:
        return get_task_snapshot(task_id)

    def list_for_session(self, session_id: str) -> list[dict[str, Any]]:
        return list_task_snapshots(session_id)


__all__ = [
    "BgTaskRepositoryAdapter",
    "get_task_snapshot",
    "list_task_snapshots",
    "reconcile_interrupted_tasks",
    "save_task_snapshot",
]
