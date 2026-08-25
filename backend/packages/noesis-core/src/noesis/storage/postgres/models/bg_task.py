"""后台子 Agent 任务快照 ORM（重启后可查询/对账）。"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from noesis.storage.postgres.base import Base


class TBackgroundTask(Base):
    """一个后台子 Agent 任务的最新快照；执行面仍在进程内，本表用于持久可见。"""

    __tablename__ = "t_bg_task"
    __table_args__ = (
        Index("idx_bg_task_session_started", "session_id", "started_at"),
        {
            "comment": "后台子 Agent 任务快照（对齐 deepagents async_tasks channel 的持久化诉求）"
        },
    )

    task_id: Mapped[str] = mapped_column(
        String(40), primary_key=True, comment="任务 ID（bg-*）"
    )
    child_session_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey('t_chat_session.id', ondelete='CASCADE'), nullable=True, comment="子 Agent 会话 ID（与内部 task_id 分离）"
    )
    run_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey('t_agent_run.id', ondelete='SET NULL'), nullable=True, comment="标准 t_agent_run ID"
    )
    session_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="所属会话"
    )
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, comment="归属用户 UUID")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="子目标描述")
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="shell（仅后台命令任务）"
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, comment="running/awaiting_approval/终态"
    )
    result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="开始毫秒"
    )
    completed_at: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True, comment="终态毫秒"
    )
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
