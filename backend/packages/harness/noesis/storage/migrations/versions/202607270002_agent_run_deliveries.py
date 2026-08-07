"""agent run delivery results

Revision ID: 202607270002
Revises: 202607270001
Create Date: 2026-07-27
"""

from alembic import op
import sqlalchemy as sa


revision = "202607270002"
down_revision = "202607270001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "t_agent_delivery",
        sa.Column("id", sa.VARCHAR(length=36), nullable=False),
        sa.Column("run_id", sa.VARCHAR(length=36), nullable=False),
        sa.Column("delivery_type", sa.VARCHAR(length=30), nullable=False),
        sa.Column("status", sa.VARCHAR(length=20), nullable=False),
        sa.Column("error_code", sa.VARCHAR(length=80), nullable=True),
        sa.Column("error_message", sa.VARCHAR(length=500), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("finished_at", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["t_agent_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="Agent run 外部平台投递结果",
    )
    op.create_index("idx_agent_delivery_run", "t_agent_delivery", ["run_id", "created_at"])
    op.create_index("idx_agent_delivery_status", "t_agent_delivery", ["status", "updated_at"])


def downgrade() -> None:
    op.drop_index("idx_agent_delivery_status", table_name="t_agent_delivery")
    op.drop_index("idx_agent_delivery_run", table_name="t_agent_delivery")
    op.drop_table("t_agent_delivery")
