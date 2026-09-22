"""定时任务运行记录重启对账集成测试（Phase 2：scheduled run 收口）。

覆盖生产事故暴露的缺口：调度器在进程内后台执行整个 agent
（``_run_in_background``，含交付链收口等待），重启即丢失执行体；``claim_due_tasks`` 已推进
``next_run_at``（下次触发照常），但 queued/running 的运行记录行无人收口
——设置页永久显示「running」，任务行 last_status 同步卡死。

契约：启动对账把遗留的 queued/running 收口为 interrupted
（error_category=server_restart）；终态行（succeeded/failed/cancelled）不动；
幂等（重复对账零效果）。

前置：``cd backend && set -a && source .env && set +a`` 后
``NOESIS_LIVE_POSTGRES_TEST=1 uv run pytest tests/test_scheduled_task_restart_reconcile.py -m integration``
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("NOESIS_LIVE_POSTGRES_TEST") != "1",
        reason="设置 NOESIS_LIVE_POSTGRES_TEST=1 后运行真实 PostgreSQL 定时任务对账集成测试",
    ),
]


async def test_scheduled_run_restart_reconcile() -> None:
    from noesis.services.scheduled_task_service import ScheduledTaskService
    from noesis.storage.postgres.manager import pg_manager
    from noesis.storage.postgres.models.scheduled_task import TUserScheduledTask
    from noesis.storage.postgres.models.settings import TUserScheduledTaskRun

    pg_manager.initialize()
    now = int(time.time() * 1000)
    task_id = str(uuid.uuid4())
    runs: dict[str, str] = {}

    async with pg_manager.get_async_session_context() as db:
        from sqlalchemy import select

        from noesis.storage.postgres.models.auth import TUser

        # demo 账号（测试专用）：user_id 列为 UUID 类型，须取真实主键
        user_id = (
            await db.execute(select(TUser.id).where(TUser.username == "test"))
        ).scalar_one()
        db.add(TUserScheduledTask(
            id=task_id, user_id=user_id, name="对账测试",
            cron_expr="0 4 * * *", timezone="Asia/Shanghai", enabled=True,
            qa_type="SUPER_AGENT_QA", prompt="x",
            session_binding="none", delivery="none",
            next_run_at=now + 3600_000, last_status="running",
            created_at=now, updated_at=now,
        ))
        for status in ("running", "queued", "succeeded"):
            run_id = str(uuid.uuid4())
            runs[status] = run_id
            db.add(TUserScheduledTaskRun(
                id=run_id, task_id=task_id, user_id=user_id, status=status,
                trigger_source="schedule", idempotency_key=f"{task_id}:{status}:{uuid.uuid4().hex[:8]}",
                created_at=now,
            ))
        await db.commit()

    try:
        async with pg_manager.get_async_session_context() as db:
            interrupted = await ScheduledTaskService.reconcile_interrupted_runs(db)
        assert interrupted == 2, "running + queued 两行应收口为 interrupted"

        async with pg_manager.get_async_session_context() as db:
            statuses = {
                r.id: r.status
                for r in (
                    await db.execute(
                        TUserScheduledTaskRun.__table__.select().where(
                            TUserScheduledTaskRun.task_id == task_id
                        )
                    )
                ).all()
            }
            assert statuses[runs["running"]] == "interrupted"
            assert statuses[runs["queued"]] == "interrupted"
            assert statuses[runs["succeeded"]] == "succeeded", "终态行不得被对账改写"

            task = (
                await db.execute(
                    TUserScheduledTask.__table__.select().where(
                        TUserScheduledTask.id == task_id
                    )
                )
            ).one()
            assert task.last_status == "interrupted", "任务行 last_status 须同步收口"
            assert task.last_error, "收口须携带可读错误信息"

        # 幂等：重复对账零效果
        async with pg_manager.get_async_session_context() as db:
            assert await ScheduledTaskService.reconcile_interrupted_runs(db) == 0
    finally:
        from sqlalchemy import delete

        async with pg_manager.get_async_session_context() as db:
            await db.execute(
                delete(TUserScheduledTaskRun).where(
                    TUserScheduledTaskRun.task_id == task_id
                )
            )
            await db.execute(
                delete(TUserScheduledTask).where(TUserScheduledTask.id == task_id)
            )
            await db.commit()
