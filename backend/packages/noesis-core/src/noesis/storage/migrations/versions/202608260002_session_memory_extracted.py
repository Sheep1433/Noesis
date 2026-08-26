"""Add memory_extracted_at to t_chat_session (md-memory-layer).

Revision ID: 202608260002
Revises: 202608260001
Create Date: 2026-08-26
"""

import sqlalchemy as sa
from alembic import op

revision = "202608260002"
down_revision = "202608260001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "t_chat_session",
        sa.Column(
            "memory_extracted_at",
            sa.BigInteger(),
            nullable=True,
            comment="记忆抽取完成时间戳（毫秒），NULL=未抽取；见 md-memory-layer",
        ),
    )


def downgrade() -> None:
    op.drop_column("t_chat_session", "memory_extracted_at")
