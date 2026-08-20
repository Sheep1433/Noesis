"""add t_bg_task table for background subagent task snapshots

Revision ID: 202608200001
Revises: 202608190001
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa

revision = "202608200001"
down_revision = "202608190001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "t_bg_task",
        sa.Column(
            "task_id", sa.VARCHAR(40), primary_key=True, comment="任务 ID（bg-*）"
        ),
        sa.Column("session_id", sa.VARCHAR(64), nullable=False, comment="所属会话"),
        sa.Column("user_id", sa.VARCHAR(64), nullable=False, comment="归属用户"),
        sa.Column("description", sa.Text(), nullable=False, comment="子目标描述"),
        sa.Column(
            "kind", sa.VARCHAR(16), nullable=False, comment="continuable | one_shot"
        ),
        sa.Column(
            "status",
            sa.VARCHAR(24),
            nullable=False,
            comment="running/awaiting_approval/终态",
        ),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.BigInteger(), nullable=False, comment="开始毫秒"),
        sa.Column("completed_at", sa.BigInteger(), nullable=True, comment="终态毫秒"),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        comment="后台子 Agent 任务快照（对齐 deepagents async_tasks channel 的持久化诉求）",
    )
    op.create_index(
        "idx_bg_task_session_started", "t_bg_task", ["session_id", "started_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_bg_task_session_started", table_name="t_bg_task")
    op.drop_table("t_bg_task")
