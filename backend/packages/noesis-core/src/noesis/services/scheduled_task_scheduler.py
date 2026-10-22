"""进程内定时任务调度循环。"""
from __future__ import annotations

import asyncio
from typing import Optional

from noesis.runtime.logging import logger
from noesis.storage.postgres.manager import pg_manager
from noesis.services.scheduled_task_service import ScheduledTaskService

_POLL_SECONDS = 30.0
_task: Optional[asyncio.Task] = None


async def _tick_once() -> None:
    """领到期任务、建 queued 记录后立即返回；执行 fire-and-forget。

    agent 执行不 await 在 tick 里——调度器与 dispatcher/SSE 共享 leader
    事件循环，一个 30 分钟的定时任务会拖垮同批到期任务与全部 Web 实时面
    （Phase 3：tick 只领任务，执行甩出主循环等待路径）。执行体
    _run_in_background 自带独立 db session 与交付链完成等待。
    """
    async with pg_manager.get_async_session_context() as db:
        try:
            rows = await ScheduledTaskService.claim_due_tasks(db, limit=20)
        except Exception:
            logger.exception("scheduled task claim failed")
            return
        for row in rows:
            try:
                run = await ScheduledTaskService._create_run_record(
                    db,
                    row,
                    trigger_source="schedule",
                    idempotency_key=f"schedule:{row.id}:{row.next_run_at}",
                )
            except Exception:
                logger.exception("scheduled task run record create failed task_id={}", row.id)
                continue
            asyncio.create_task(
                ScheduledTaskService._run_in_background(row.id, row.user_id, run.id)
            )
            await ScheduledTaskService.cleanup_runs(db, row.user_id)


async def _loop() -> None:
    logger.info("scheduled task scheduler started poll={}s", _POLL_SECONDS)
    while True:
        try:
            await _tick_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scheduled task scheduler tick error")
        await asyncio.sleep(_POLL_SECONDS)


def start_scheduled_task_scheduler() -> None:
    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_loop(), name="user-scheduled-tasks")


async def stop_scheduled_task_scheduler() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except (asyncio.CancelledError, Exception):
        pass
    _task = None
