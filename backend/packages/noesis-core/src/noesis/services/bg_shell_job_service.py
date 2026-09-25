"""shell job 事实行服务（bg_shell_job 表）：落库、投影、重启对账。

shell 任务非对话：无 child session / run 行可挂靠，本服务是任务事实在
DB 侧的唯一写入与读取边界。内存热集回收或跨进程查询由本表回答；进程
重启对账把非终态行（queued / running）收口为 cancelled——shell 执行环境
（local_shell 宿主机 / docker 会话容器）不持久化，queued 亦不重建。
agents 侧经 ShellJobPort 访问（services → agents 方向注册）。
"""
from __future__ import annotations

import time
from typing import Any, Optional

from sqlalchemy import select, update

from noesis.ids import now_ms
from noesis.agents.background.jobs.state import BgTaskStatus
from noesis.agents.background.ports import configure_shell_job_port
from noesis.runtime.logging import logger
from noesis.storage.postgres.models.bg_task import TBgShellJob


def _to_seconds(ms: Optional[int]) -> Optional[float]:
    return (ms / 1000) if ms else None


class BgShellJobService:
    """bg_shell_job 行的唯一写入边界（端口实现）。"""

    @classmethod
    async def persist_start(
        cls,
        *,
        task_id: str,
        session_id: str,
        user_id: str,
        command: str,
        status: str,
    ) -> None:
        from noesis.storage.postgres.manager import pg_manager

        now = now_ms()
        async with pg_manager.get_async_session_context() as db:
            db.add(TBgShellJob(
                task_id=task_id,
                session_id=session_id,
                user_id=str(user_id),
                command=command,
                status=status,
                created_at=now,
                started_at=now if status == BgTaskStatus.RUNNING.value else None,
            ))
            await db.commit()

    @classmethod
    async def mark_started(cls, task_id: str) -> None:
        from noesis.storage.postgres.manager import pg_manager

        now = now_ms()
        async with pg_manager.get_async_session_context() as db:
            await db.execute(
                update(TBgShellJob)
                .where(TBgShellJob.task_id == task_id, TBgShellJob.status == BgTaskStatus.QUEUED.value)
                .values(status=BgTaskStatus.RUNNING.value, started_at=now)
            )
            await db.commit()

    @classmethod
    async def mark_terminal(
        cls,
        *,
        task_id: str,
        status: str,
        error: Optional[str],
        result_tail: Optional[str],
        completed_at: Optional[float],
    ) -> None:
        from noesis.storage.postgres.manager import pg_manager

        if not BgTaskStatus(status).is_terminal:
            return
        async with pg_manager.get_async_session_context() as db:
            await db.execute(
                update(TBgShellJob)
                .where(
                    TBgShellJob.task_id == task_id,
                    TBgShellJob.status.in_([
                        BgTaskStatus.QUEUED.value, BgTaskStatus.RUNNING.value,
                    ]),
                )
                .values(
                    status=status,
                    error=error,
                    result_tail=result_tail,
                    completed_at=int((completed_at or time.time()) * 1000),
                )
            )
            await db.commit()

    @classmethod
    async def update_output_tail(cls, task_id: str, tail: str) -> None:
        """运行中输出尾部快照 flush（执行进程周期调用；幂等覆盖写）。"""
        from noesis.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as db:
            await db.execute(
                update(TBgShellJob)
                .where(
                    TBgShellJob.task_id == task_id,
                    # 终态守卫：迟到的最后一次 flush 不覆盖终态事实行
                    TBgShellJob.status.in_([
                        BgTaskStatus.QUEUED.value, BgTaskStatus.RUNNING.value,
                    ]),
                )
                .values(output_tail=tail)
            )
            await db.commit()

    @classmethod
    async def get_task(cls, task_id: str) -> Optional[dict[str, Any]]:
        from noesis.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as db:
            row = (
                await db.execute(select(TBgShellJob).where(TBgShellJob.task_id == task_id))
            ).scalar_one_or_none()
            return cls._project(row) if row is not None else None

    @classmethod
    async def list_for_session(cls, session_id: str) -> list[dict[str, Any]]:
        from noesis.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as db:
            rows = (
                await db.execute(
                    select(TBgShellJob)
                    .where(TBgShellJob.session_id == session_id)
                    .order_by(TBgShellJob.created_at.asc())
                )
            ).scalars().all()
            return [cls._project(row) for row in rows]

    @classmethod
    async def reconcile_orphaned(cls, db: Any = None) -> int:
        """非终态 shell 行收口为 cancelled（进程重启、产出未知）。

        幂等：仅 queued / running 行受影响；queued 亦不重建（执行环境不
        持久化，重启后无从获取会话沙箱 backend）。
        """
        own_db = db is None
        if own_db:
            from noesis.storage.postgres.manager import pg_manager
            ctx = pg_manager.get_async_session_context()
        else:
            ctx = _PseudoCtx(db)
        async with ctx as session:
            now = now_ms()
            result = await session.execute(
                update(TBgShellJob)
                .where(TBgShellJob.status.in_([
                    BgTaskStatus.QUEUED.value, BgTaskStatus.RUNNING.value,
                ]))
                .values(
                    status=BgTaskStatus.CANCELLED.value,
                    error="后端进程重启，后台命令已中断（产出未知）",
                    completed_at=now,
                )
            )
            if own_db:
                await session.commit()
        count = result.rowcount or 0
        if count:
            logger.warning("bg shell jobs cancelled on restart count={}", count)
        return count

    @staticmethod
    def _project(row: TBgShellJob) -> dict[str, Any]:
        return {
            "task_id": row.task_id,
            "session_id": row.session_id,
            "user_id": row.user_id,
            "description": row.command,
            "command": row.command,
            "kind": "shell",
            "subagent_type": None,
            "status": row.status,
            "result": row.result_tail,
            "output_tail": row.output_tail,
            "error": row.error,
            "started_at": _to_seconds(row.started_at),
            "completed_at": _to_seconds(row.completed_at),
            "progress_count": 0,
            "undelivered_messages": 0,
        }


class _PseudoCtx:
    """复用调用方事务的轻量 async 上下文（对账在同一事务内执行）。"""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def __aenter__(self) -> Any:
        return self._db

    async def __aexit__(self, *exc: Any) -> None:
        return None


configure_shell_job_port(BgShellJobService)

__all__ = ["BgShellJobService"]
