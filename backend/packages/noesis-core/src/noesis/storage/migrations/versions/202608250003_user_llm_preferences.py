"""add user_llm_preferences table for user-level default model

Revision ID: 202608250003
Revises: 202608250002
Create Date: 2026-08-25
"""

from alembic import op
import sqlalchemy as sa

revision = "202608250003"
down_revision = "202608250002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_llm_preferences",
        sa.Column("user_id", sa.Uuid(), primary_key=True, comment="用户 UUID"),
        sa.Column(
            "default_model_id",
            sa.String(240),
            nullable=True,
            comment="默认模型 id（内置裸 id 或自定义复合 id）",
        ),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["t_user.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("user_llm_preferences")
