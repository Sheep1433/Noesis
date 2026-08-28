"""add reasoning_levels capability column to user_llm_models

Revision ID: 202608280001
Revises: 202608270001
Create Date: 2026-08-28
"""

from alembic import op
import sqlalchemy as sa

revision = "202608280001"
down_revision = "202608270001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_llm_models",
        sa.Column(
            "reasoning_levels",
            sa.String(64),
            nullable=True,
            comment="推理档位能力声明（逗号分隔子集 off,low,medium,high,max；NULL=未声明不显示控件）",
        ),
    )


def downgrade() -> None:
    op.drop_column("user_llm_models", "reasoning_levels")
