"""durable agent runs

Revision ID: 202607270001
Revises: 202607260002
Create Date: 2026-07-27
"""

from alembic import op
import sqlalchemy as sa


revision = "202607270001"
down_revision = "202607260002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "t_agent_run",
        sa.Column("id", sa.VARCHAR(length=36), nullable=False),
        sa.Column("user_id", sa.VARCHAR(length=36), nullable=False),
        sa.Column("session_id", sa.VARCHAR(length=36), nullable=False),
        sa.Column("assistant_message_id", sa.VARCHAR(length=36), nullable=False),
        sa.Column("client_request_id", sa.VARCHAR(length=64), nullable=False),
        sa.Column("request_digest", sa.VARCHAR(length=64), nullable=False),
        sa.Column("qa_type", sa.VARCHAR(length=40), nullable=False),
        sa.Column("origin", sa.VARCHAR(length=20), nullable=False),
        sa.Column("status", sa.VARCHAR(length=20), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("attempt_id", sa.Integer(), nullable=False),
        sa.Column("finish_reason", sa.VARCHAR(length=40), nullable=True),
        sa.Column("error_code", sa.VARCHAR(length=80), nullable=True),
        sa.Column("user_error_message", sa.VARCHAR(length=500), nullable=True),
        sa.Column("retry_attempt", sa.Integer(), nullable=False),
        sa.Column("retry_max", sa.Integer(), nullable=False),
        sa.Column("owner_instance_id", sa.VARCHAR(length=100), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("finished_at", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["assistant_message_id"], ["t_chat_message.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["t_chat_session.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assistant_message_id"),
        sa.UniqueConstraint("user_id", "client_request_id", name="uq_agent_run_user_request"),
        comment="Agent run 生命周期与恢复快照",
    )
    op.create_index("idx_agent_run_owner_status", "t_agent_run", ["owner_instance_id", "status"])
    op.create_index("idx_agent_run_session_status", "t_agent_run", ["session_id", "status"])
    op.create_index("idx_agent_run_updated", "t_agent_run", ["updated_at"])
    op.create_index(
        "uq_agent_run_active_session",
        "t_agent_run",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running','retrying','hitl_pending')"),
    )


def downgrade() -> None:
    op.drop_index("uq_agent_run_active_session", table_name="t_agent_run")
    op.drop_index("idx_agent_run_updated", table_name="t_agent_run")
    op.drop_index("idx_agent_run_session_status", table_name="t_agent_run")
    op.drop_index("idx_agent_run_owner_status", table_name="t_agent_run")
    op.drop_table("t_agent_run")
