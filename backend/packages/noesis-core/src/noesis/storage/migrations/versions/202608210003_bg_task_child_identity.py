"""persist child session and standard run identity in transitional job snapshots

Revision ID: 202608210003
Revises: 202608210002
Create Date: 2026-08-21
"""

from alembic import op
import sqlalchemy as sa


revision = "202608210003"
down_revision = "202608210002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "t_bg_task",
        sa.Column("child_session_id", sa.VARCHAR(36), nullable=True, comment="子 Agent 会话 ID"),
    )
    op.add_column(
        "t_bg_task",
        sa.Column("run_id", sa.VARCHAR(36), nullable=True, comment="标准 t_agent_run ID"),
    )
    op.create_foreign_key(
        "fk_bg_task_child_session",
        "t_bg_task",
        "t_chat_session",
        ["child_session_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_bg_task_run",
        "t_bg_task",
        "t_agent_run",
        ["run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("idx_bg_task_child_session", "t_bg_task", ["child_session_id"], unique=True)


def downgrade() -> None:
    op.drop_index("idx_bg_task_child_session", table_name="t_bg_task")
    op.drop_constraint("fk_bg_task_run", "t_bg_task", type_="foreignkey")
    op.drop_constraint("fk_bg_task_child_session", "t_bg_task", type_="foreignkey")
    op.drop_column("t_bg_task", "run_id")
    op.drop_column("t_bg_task", "child_session_id")
