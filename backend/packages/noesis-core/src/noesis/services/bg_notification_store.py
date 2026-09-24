"""后台任务终态通知的持久化实现与启动恢复。

模块 import 即向 ``agents.background.ports`` 注册 NotificationStorePort
实现（与其他服务端口同款装配方式）。恢复入口
``restore_undelivered_notifications`` 在进程启动（leader-only 对账块）调用：
清掉超期孤儿行（会话已死的未送达通知）后，把其余未送达行按会话、按
created_at 升序装载回内存注册表。
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.ids import now_ms
from noesis.agents.background import notifications
from noesis.agents.background.ports import configure_notification_store
from noesis.runtime.logging import logger
from noesis.storage.postgres.models.bg_task import TBgTaskNotification

# 未送达通知的保留上限：会话长期无 run 消费（已删除/废弃）时行在此窗口后
# 由恢复入口清理，防表无限增长
_RETENTION_MS = 30 * 24 * 60 * 60 * 1000


class BgNotificationStore:
    """NotificationStorePort 实现：落库 / 送达删行（各自独立 session）。"""

    @staticmethod
    async def persist(session_id: str, notice: dict[str, Any]) -> None:
        from noesis.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as db:
            db.add(TBgTaskNotification(
                id=str(notice["id"]),
                session_id=session_id,
                payload=notice,
                created_at=now_ms(),
            ))
            await db.commit()

    @staticmethod
    async def delete_delivered(session_id: str, notice_ids: list[str]) -> None:
        from noesis.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as db:
            await db.execute(
                delete(TBgTaskNotification).where(
                    TBgTaskNotification.session_id == session_id,
                    TBgTaskNotification.id.in_(notice_ids),
                )
            )
            await db.commit()


async def restore_undelivered_notifications(db: AsyncSession | None = None) -> int:
    """启动恢复：DB 未送达通知装载回内存注册表，返回实际装载数。

    先清超期孤儿行（超过保留窗口仍未送达——会话已无消费可能）；再按
    created_at 升序装载，保证同会话通知的注入顺序与产生顺序一致。
    幂等：内存已有同 id 通知时跳过（notifications.load_persisted）。
    """
    if db is not None:
        return await _restore_with_session(db)

    from noesis.storage.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as session:
        return await _restore_with_session(session)


async def _restore_with_session(db: AsyncSession) -> int:
    now = now_ms()
    expired = await db.execute(
        delete(TBgTaskNotification).where(
            TBgTaskNotification.created_at < now - _RETENTION_MS
        )
    )
    result = await db.execute(
        select(TBgTaskNotification.session_id, TBgTaskNotification.payload)
        .order_by(TBgTaskNotification.created_at.asc())
    )
    rows = [(row.session_id, dict(row.payload)) for row in result.all()]
    await db.commit()
    loaded = notifications.load_persisted(rows)
    if loaded or (expired.rowcount or 0):
        logger.info(
            "bg 通知启动恢复 loaded={} expired={} pending_rows={}",
            loaded, expired.rowcount or 0, len(rows),
        )
    return loaded


configure_notification_store(BgNotificationStore)
