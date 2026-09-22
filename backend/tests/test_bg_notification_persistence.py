"""后台任务终态通知持久化集成测试（Phase 2：通知落库 + 启动恢复）。

覆盖生产事故暴露的缺口：通知只存内存，无人值守会话（定时任务）在进程
重启后未送达通知蒸发，依赖通知驱动的收果链断裂。契约：

1. record() 异步落库（fire-and-forget，经主 loop）——重启后可恢复；
2. 启动恢复把 DB 未送达行装载回内存注册表，注入链照常工作；
3. take_undelivered 送达后删行——表只存未送达，无无限增长；
4. 恢复幂等：重复 restore 不产生重复注入。

与 test_subagent_stop_real_db 同款约束：单文件单测试函数（pg 引擎池与
捕获的主 loop 绑定首个 loop，跨函数换 loop 会炸连接池）。

前置：``cd backend && set -a && source .env && set +a`` 后
``NOESIS_LIVE_POSTGRES_TEST=1 uv run pytest tests/test_bg_notification_persistence.py -m integration``
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("NOESIS_LIVE_POSTGRES_TEST") != "1",
        reason="设置 NOESIS_LIVE_POSTGRES_TEST=1 后运行真实 PostgreSQL 通知持久化集成测试",
    ),
]


async def _poll(check, timeout: float = 5.0, what: str = "condition") -> None:
    """轮询等待异步条件成立（check 返回协程）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if await check():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"等待超时：{what}")


async def test_notification_survives_process_restart() -> None:
    from sqlalchemy import delete

    from noesis.agents.background import notifications
    from noesis.runtime.main_loop import capture_main_loop
    from noesis.storage.postgres.manager import pg_manager
    from noesis.storage.postgres.models.bg_task import TBgTaskNotification

    capture_main_loop()
    pg_manager.initialize()
    # 服务模块 import 即注册通知存储端口（生产在 lifespan 完成）
    import noesis.services.bg_notification_store as store_mod

    session_id = f"bg-notice-test-{uuid.uuid4().hex[:8]}"
    try:
        # 1. record 落库：fire-and-forget，轮询等行出现
        notifications.record(
            session_id, task_id="task-1", status="completed",
            preview="调研结论预览", label="市场调研", step_count=3,
            duration_ms=1500, turn_count=2,
        )
        await _poll(
            lambda: _has_row(session_id), what="record 后 DB 出现未送达通知行",
        )

        # 2. 模拟重启：内存注册表清空（进程重启即内存蒸发）
        assert notifications.drain(session_id), "record 应先写入内存"
        assert notifications.take_undelivered(session_id) == [], "内存清空后无可注入通知"

        # 3. 启动恢复：DB 未送达行装载回内存
        restored = await store_mod.restore_undelivered_notifications()
        assert restored >= 1, "至少恢复本测试写入的一条通知"
        notices = notifications.take_undelivered(session_id)
        assert len(notices) == 1, "恢复后恰好一条可注入通知"
        notice = notices[0]
        assert notice["status"] == "completed"
        assert notice["label"] == "市场调研"
        assert notice["preview"] == "调研结论预览"
        assert notice["step_count"] == 3

        # 4. 恢复幂等：重复 restore 不产生重复注入
        await store_mod.restore_undelivered_notifications()
        assert notifications.take_undelivered(session_id) == [], "重复恢复不得重复注入"

        # 5. 送达删行：表只存未送达
        await _poll(
            lambda: _row_gone(session_id), what="送达后 DB 行删除",
        )
    finally:
        async with pg_manager.get_async_session_context() as db:
            await db.execute(
                delete(TBgTaskNotification).where(
                    TBgTaskNotification.session_id == session_id
                )
            )
            await db.commit()
        notifications.drain(session_id)


async def _has_row(session_id: str) -> bool:
    return not await _row_gone(session_id)


async def _row_gone(session_id: str) -> bool:
    from sqlalchemy import select

    from noesis.storage.postgres.manager import pg_manager
    from noesis.storage.postgres.models.bg_task import TBgTaskNotification

    async with pg_manager.get_async_session_context() as db:
        row = (
            await db.execute(
                select(TBgTaskNotification.id).where(
                    TBgTaskNotification.session_id == session_id
                )
            )
        ).scalar_one_or_none()
        return row is None
