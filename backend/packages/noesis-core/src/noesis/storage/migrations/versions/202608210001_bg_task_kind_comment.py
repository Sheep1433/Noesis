"""align background task kind comment with unified subagent lifecycle

Revision ID: 202608210001
Revises: 202608200001
Create Date: 2026-08-21
"""

from alembic import op
import sqlalchemy as sa

revision = "202608210001"
down_revision = "202608200001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "t_bg_task",
        "kind",
        existing_type=sa.VARCHAR(16),
        existing_nullable=False,
        comment="shell（仅后台命令任务）",
    )


def downgrade() -> None:
    op.alter_column(
        "t_bg_task",
        "kind",
        existing_type=sa.VARCHAR(16),
        existing_nullable=False,
        comment="shell",
    )
