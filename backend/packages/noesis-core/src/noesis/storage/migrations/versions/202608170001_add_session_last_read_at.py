"""add last_read_at column to t_chat_session

Revision ID: 202608170001
Revises: 202607300001
Create Date: 2026-08-17
"""

from alembic import op
import sqlalchemy as sa

revision = "202608170001"
down_revision = "202607300001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "t_chat_session",
        sa.Column("last_read_at", sa.BigInteger(), nullable=True, comment="用户最后查看会话的时间戳（毫秒），NULL=从未读过；updated_at > last_read_at 表示有未读"),
    )


def downgrade() -> None:
    op.drop_column("t_chat_session", "last_read_at")
