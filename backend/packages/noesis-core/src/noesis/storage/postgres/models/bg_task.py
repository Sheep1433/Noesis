"""后台任务终态通知 ORM（未送达队列的持久层）。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, BigInteger, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from noesis.storage.postgres.base import Base


class TBgTaskNotification(Base):
    """一条未送达的后台任务终态通知。

    表内只存未送达行：注入（take_undelivered）后即删，无 delivered 标记
    与历史堆积。进程重启后由启动恢复（restore_undelivered_notifications）
    装载回内存注册表，通知链跨重启存活。
    """

    __tablename__ = "bg_task_notifications"
    __table_args__ = (Index("idx_bg_task_notifications_session", "session_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, comment="通知 ID（与内存 notice id 同源）")
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="主会话 ID")
    # 通知载荷（label/status/preview/step_count/duration_ms/turn_count/sources）
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="创建毫秒")
