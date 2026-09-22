"""后台任务持久层 ORM：未送达通知队列 + shell job 事实行。"""
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


class TBgShellJob(Base):
    """一条后台命令任务（kind="shell"）的事实行。

    shell 任务无 child session / run 行可挂靠（非对话），本表是其唯一的
    DB 事实承载：状态机、命令、结果尾部摘要与时间戳。内存热集回收或
    跨进程查询时由本表回答；进程重启对账把非终态行收口为 cancelled。
    status 使用 BgTaskStatus 值域（queued/running/completed/failed/
    cancelled/timed_out）。
    """

    __tablename__ = "bg_shell_job"
    __table_args__ = (
        Index("idx_bg_shell_job_session", "session_id", "created_at"),
        Index("idx_bg_shell_job_status", "status"),
    )

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="任务 ID（bg-*，与内存 task_id 同源）")
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="主会话 ID")
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="所属用户")
    command: Mapped[str] = mapped_column(String, nullable=False, comment="原始命令（展示与执行同源）")
    status: Mapped[str] = mapped_column(String(16), nullable=False, comment="任务状态（BgTaskStatus 值域）")
    error: Mapped[str | None] = mapped_column(String, nullable=True, comment="失败/取消原因")
    # 结果尾部摘要（stdout/stderr 尾部，与通知预览同源）
    result_tail: Mapped[str | None] = mapped_column(String, nullable=True, comment="结果尾部摘要（有界）")
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="创建毫秒（排队重建排序键）")
    started_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="开始执行毫秒")
    completed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="终态毫秒")
