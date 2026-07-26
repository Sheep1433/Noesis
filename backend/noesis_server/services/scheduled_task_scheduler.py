"""进程内定时任务调度循环。"""
from __future__ import annotations

import asyncio
from typing import Optional

from noesis.runtime.logging import logger
from noesis_server.infrastructure.database.engine import AsyncSessionLocal
from noesis_server.services.scheduled_task_service import ScheduledTaskService

_POLL_SECONDS = 30.0
_task: Optional[asyncio.Task] = None


async def _tick_once() -> None:
    async with AsyncSessionLocal() as db:
        try:
            rows = await ScheduledTaskService.claim_due_tasks(db, limit=20)
        except Exception:
            logger.exception("scheduled task claim failed")
            return
        for row in rows:
            from noesis_server.services.scheduled_task_service import _now_ms
            run = await ScheduledTaskService.execute_with_record(
                db,
                row,
                trigger_source="schedule",
                idempotency_key=f"schedule:{row.id}:{row.next_run_at}",
            )
            row.last_status = run.status
            row.last_error = run.error_message
            row.last_run_at = _now_ms()
            row.updated_at = row.last_run_at
            await ScheduledTaskService.cleanup_runs(db, row.user_id)
        if rows:
            await db.commit()


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
