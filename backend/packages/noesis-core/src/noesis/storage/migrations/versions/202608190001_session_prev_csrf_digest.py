"""add prev_csrf_digest column to t_user_session

Revision ID: 202608190001
Revises: 202608170001
Create Date: 2026-08-19
"""

from alembic import op
import sqlalchemy as sa

revision = "202608190001"
down_revision = "202608170001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "t_user_session",
        sa.Column("prev_csrf_digest", sa.VARCHAR(64), nullable=True, comment="上一次轮换前的 CSRF Token 摘要（宽容旧窗口一代 token）"),
    )


def downgrade() -> None:
    op.drop_column("t_user_session", "prev_csrf_digest")
