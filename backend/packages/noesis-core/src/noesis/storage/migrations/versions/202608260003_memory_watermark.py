"""Add memory_extracted_seq watermark to t_chat_session (md-memory-layer).

Revision ID: 202608260003
Revises: 202608260002
Create Date: 2026-08-26

水位增量抽取：memory_extracted_seq = 已成功抽取到的最大
message_sequence（NULL = 从未抽取）；续聊后仅抽取水位之后的新消息段。
memory_extracted_at 降级为纯观测字段（最近一次抽取时间）。
"""

import sqlalchemy as sa
from alembic import op

revision = "202608260003"
down_revision = "202608260002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "t_chat_session",
        sa.Column(
            "memory_extracted_seq",
            sa.BigInteger(),
            nullable=True,
            comment="记忆抽取水位：已成功抽取的最大消息序号；NULL=从未抽取",
        ),
    )


def downgrade() -> None:
    op.drop_column("t_chat_session", "memory_extracted_seq")
