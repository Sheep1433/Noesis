"""add server-side user sessions

Revision ID: 202607120001
Revises: 202606300001
Create Date: 2026-07-12
"""

from alembic import op
import sqlalchemy as sa

revision = "202607120001"
down_revision = "202606300001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "t_user_session",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False, comment="会话公开标识"),
        sa.Column("user_id", sa.Integer(), nullable=False, comment="用户 ID"),
        sa.Column("session_digest", sa.String(64), nullable=False, unique=True, comment="Session SHA-256 摘要"),
        sa.Column("csrf_digest", sa.String(64), nullable=False, comment="CSRF Token SHA-256 摘要"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("last_seen_at", sa.BigInteger(), nullable=False),
        sa.Column("idle_expires_at", sa.BigInteger(), nullable=False),
        sa.Column("absolute_expires_at", sa.BigInteger(), nullable=False),
        sa.Column("revoked_at", sa.BigInteger(), nullable=True),
        sa.Column("device_name", sa.String(200), nullable=True),
        sa.Column("user_agent_digest", sa.String(64), nullable=True),
        sa.Column("last_ip", sa.String(64), nullable=True),
    )
    op.create_index("idx_user_session_user_active", "t_user_session", ["user_id", "revoked_at", "idle_expires_at"])


def downgrade() -> None:
    op.drop_index("idx_user_session_user_active", table_name="t_user_session")
    op.drop_table("t_user_session")
