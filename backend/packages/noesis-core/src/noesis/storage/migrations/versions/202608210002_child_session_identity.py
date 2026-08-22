"""make child Agent sessions first-class identities

Revision ID: 202608210002
Revises: 202608210001
Create Date: 2026-08-21
"""

from alembic import op
import sqlalchemy as sa


revision = "202608210002"
down_revision = "202608210001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "t_chat_session",
        sa.Column(
            "kind",
            sa.VARCHAR(20),
            nullable=False,
            server_default="root",
            comment="会话类型: root | subagent",
        ),
    )
    op.add_column(
        "t_chat_session",
        sa.Column(
            "created_by_run_id",
            sa.VARCHAR(36),
            nullable=True,
            comment="创建该子会话的 Agent run ID",
        ),
    )
    op.add_column(
        "t_chat_session",
        sa.Column(
            "created_by_tool_call_id",
            sa.VARCHAR(128),
            nullable=True,
            comment="创建该子会话的工具调用 ID",
        ),
    )
    op.create_foreign_key(
        "fk_chat_session_created_by_run",
        "t_chat_session",
        "t_agent_run",
        ["created_by_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        sa.text(
            "UPDATE t_chat_session SET kind = 'subagent' "
            "WHERE parent_id IS NOT NULL"
        )
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_chat_session_created_by_run",
        "t_chat_session",
        type_="foreignkey",
    )
    op.drop_column("t_chat_session", "created_by_tool_call_id")
    op.drop_column("t_chat_session", "created_by_run_id")
    op.drop_column("t_chat_session", "kind")
