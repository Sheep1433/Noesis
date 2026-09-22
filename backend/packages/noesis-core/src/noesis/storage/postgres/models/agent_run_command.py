"""Agent Run 跨进程命令 ORM（stop / HITL resume / 后台任务停止）。"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import JSON, BigInteger, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from noesis.storage.postgres.base import Base


class TAgentRunCommand(Base):
    """一条 durable command：任意 worker 提交落库，leader consumer 认领执行。

    幂等去重：``(user_id, dedupe_key)`` 唯一——stop 按 ``run:{run_id}:stop``、
    HITL 按 ``run:{run_id}:hitl:{interrupt_id}``（decision digest 冲突返回
    409，相同 digest 幂等返回既有命令）、后台任务停止按
    ``bg:{task_id}:stop``。保留期既是清理窗口也是去重窗口（超窗重复提交
    按新 command 重验状态）。
    """

    __tablename__ = "t_agent_run_command"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="uq_agent_run_command_dedupe"),
        Index("idx_agent_run_command_pending", "status", "created_at"),
        Index("idx_agent_run_command_run", "run_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, comment="命令 ID")
    run_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, comment="目标 Run（bg_task_stop 可为空）")
    task_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, comment="后台任务 ID（bg_task_stop）")
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False, comment="stop | hitl_resume | bg_task_stop")
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False, comment="幂等去重键")
    decision_digest: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, comment="HITL 决策摘要（sha256）")
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", comment="pending | claimed | completed | rejected | no_op")
    result_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claimed_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    completed_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
