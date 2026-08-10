"""user scheduled tasks table

Revision ID: 202607230001
Revises: 202607130003
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa

revision = "202607230001"
down_revision = "202607130003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_scheduled_tasks",
        sa.Column("id", sa.String(length=36), primary_key=True, comment="任务 ID"),
        sa.Column("user_id", sa.Integer(), nullable=False, comment="用户 ID"),
        sa.Column("name", sa.String(length=200), nullable=False, comment="显示名"),
        sa.Column("cron_expr", sa.String(length=120), nullable=False, comment="cron 表达式"),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Shanghai"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("qa_type", sa.String(length=64), nullable=False, server_default="SUPER_AGENT_QA"),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("session_binding", sa.String(length=120), nullable=False, server_default="none"),
        sa.Column("delivery", sa.String(length=120), nullable=False, server_default="none"),
        sa.Column("last_run_at", sa.BigInteger(), nullable=True),
        sa.Column("next_run_at", sa.BigInteger(), nullable=True),
        sa.Column("last_status", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("disabled_reason", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("idx_user_scheduled_tasks_user", "user_scheduled_tasks", ["user_id"])
    op.create_index(
        "idx_user_scheduled_tasks_due",
        "user_scheduled_tasks",
        ["enabled", "next_run_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_user_scheduled_tasks_due", table_name="user_scheduled_tasks")
    op.drop_index("idx_user_scheduled_tasks_user", table_name="user_scheduled_tasks")
    op.drop_table("user_scheduled_tasks")
