"""drop dead retry_attempt/retry_max columns from t_agent_run

Revision ID: 202607300001
Revises: 202607290001
Create Date: 2026-08-14
"""

from alembic import op
import sqlalchemy as sa

revision = "202607300001"
down_revision = "202607290001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("t_agent_run", "retry_attempt")
    op.drop_column("t_agent_run", "retry_max")


def downgrade() -> None:
    op.add_column("t_agent_run", sa.Column("retry_max", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("t_agent_run", sa.Column("retry_attempt", sa.Integer(), nullable=False, server_default="0"))
