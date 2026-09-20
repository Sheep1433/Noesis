"""bg_shell_job table（后台命令任务事实行）

Revision ID: 202609200001
Revises: 202609190002
Create Date: 2026-09-20
"""

from alembic import op
import sqlalchemy as sa

revision = "202609200001"
down_revision = "202609190002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bg_shell_job",
        sa.Column("task_id", sa.String(length=64), primary_key=True, comment="任务 ID（bg-*，与内存 task_id 同源）"),
        sa.Column("session_id", sa.String(length=64), nullable=False, comment="主会话 ID"),
        sa.Column("user_id", sa.String(length=64), nullable=False, comment="所属用户"),
        sa.Column("command", sa.String(), nullable=False, comment="原始命令（展示与执行同源）"),
        sa.Column("status", sa.String(length=16), nullable=False, comment="任务状态（queued/running/completed/failed/cancelled/timed_out）"),
        sa.Column("error", sa.String(), nullable=True, comment="失败/取消原因"),
        sa.Column("result_tail", sa.String(), nullable=True, comment="结果尾部摘要（有界）"),
        sa.Column("created_at", sa.BigInteger(), nullable=False, comment="创建毫秒（排队重建排序键）"),
        sa.Column("started_at", sa.BigInteger(), nullable=True, comment="开始执行毫秒"),
        sa.Column("completed_at", sa.BigInteger(), nullable=True, comment="终态毫秒"),
    )
    op.create_index("idx_bg_shell_job_session", "bg_shell_job", ["session_id", "created_at"])
    op.create_index("idx_bg_shell_job_status", "bg_shell_job", ["status"])


def downgrade() -> None:
    op.drop_index("idx_bg_shell_job_status", table_name="bg_shell_job")
    op.drop_index("idx_bg_shell_job_session", table_name="bg_shell_job")
    op.drop_table("bg_shell_job")
