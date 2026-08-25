"""add slug column to user_llm_providers

Revision ID: 202608250001
Revises: 202608240003
Create Date: 2026-08-25
"""

from alembic import op
import sqlalchemy as sa

revision = "202608250001"
down_revision = "202608240003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_llm_providers",
        sa.Column("slug", sa.String(64), nullable=False, server_default=""),
    )
    # 存量行回填：以主键前 8 位作为唯一 slug（软删除行也保持唯一）
    op.execute("UPDATE user_llm_providers SET slug = 'p-' || left(id, 8) WHERE slug = ''")
    op.create_index(
        "idx_user_llm_providers_user_slug", "user_llm_providers", ["user_id", "slug"]
    )


def downgrade() -> None:
    op.drop_index("idx_user_llm_providers_user_slug", table_name="user_llm_providers")
    op.drop_column("user_llm_providers", "slug")
