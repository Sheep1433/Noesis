"""add registration invites

Revision ID: 202607130001
Revises: 202607120001
Create Date: 2026-07-13
"""

from alembic import op

revision = "202607130001"
down_revision = "202607120001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_t_user_username", "t_user", ["username"])


def downgrade() -> None:
    op.drop_constraint("uq_t_user_username", "t_user", type_="unique")
