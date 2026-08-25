"""add private recalled-memory context to agent runs

Revision ID: 202608240003
Revises: 202608240002
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "202608240003"
down_revision = "202608240002"
branch_labels = None
depends_on = None


JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "t_agent_run",
        sa.Column(
            "memory_context",
            JSON_VALUE,
            nullable=True,
            comment="Run-private recalled memory ids/hash",
        ),
    )


def downgrade() -> None:
    op.drop_column("t_agent_run", "memory_context")
