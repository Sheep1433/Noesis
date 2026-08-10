"""session pin and archive

Revision ID: 202607260002
Revises: 202607260001
Create Date: 2026-07-26
"""

from alembic import op
import sqlalchemy as sa

revision = "202607260002"
down_revision = "202607260001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "t_chat_session",
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "t_chat_session",
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index(
        "idx_session_user_archived",
        "t_chat_session",
        ["user_id", "archived"],
    )


def downgrade() -> None:
    op.drop_index("idx_session_user_archived", table_name="t_chat_session")
    op.drop_column("t_chat_session", "archived")
    op.drop_column("t_chat_session", "pinned")
