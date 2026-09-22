"""bg task notifications table（后台任务终态通知持久化）

Revision ID: 202609190001
Revises: 202609070001
Create Date: 2026-09-19
"""

from alembic import op
import sqlalchemy as sa

revision = "202609190001"
down_revision = "202609070001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bg_task_notifications",
        sa.Column("id", sa.String(length=36), primary_key=True, comment="通知 ID（与内存 notice id 同源）"),
        sa.Column("session_id", sa.String(length=64), nullable=False, comment="主会话 ID"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False, comment="创建毫秒"),
    )
    op.create_index(
        "idx_bg_task_notifications_session",
        "bg_task_notifications",
        ["session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_bg_task_notifications_session", table_name="bg_task_notifications")
    op.drop_table("bg_task_notifications")
