"""用户定时任务 ORM。"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import BigInteger, Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from config.database import Base


class TUserScheduledTask(Base):
    __tablename__ = "user_scheduled_tasks"
    __table_args__ = (
        Index("idx_user_scheduled_tasks_user", "user_id"),
        Index("idx_user_scheduled_tasks_due", "enabled", "next_run_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, comment="任务 ID")
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="用户 ID")
    name: Mapped[str] = mapped_column(String(200), nullable=False, comment="显示名")
    cron_expr: Mapped[str] = mapped_column(String(120), nullable=False, comment="cron 表达式")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Shanghai")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    qa_type: Mapped[str] = mapped_column(String(64), nullable=False, default="SUPER_AGENT_QA")
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    session_binding: Mapped[str] = mapped_column(
        String(120), nullable=False, default="none", comment="none | session:{id}"
    )
    delivery: Mapped[str] = mapped_column(
        String(120), nullable=False, default="none", comment="none | web_notify | channel:{id}"
    )
    last_run_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, comment="上次运行毫秒")
    next_run_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, comment="下次运行毫秒")
    last_status: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    disabled_reason: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
