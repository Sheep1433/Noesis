"""agent_run claim fencing：claim_epoch + heartbeat_at（worker-role-split Phase 1）

Revision ID: 202609220001
Revises: 202609200001
Create Date: 2026-09-22
"""

from alembic import op
import sqlalchemy as sa

revision = "202609220001"
down_revision = "202609200001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 纯加列，存量行零回填：claim_epoch=0 表示 fencing 语义前认领的 run，
    # 首次对账重置或再认领后进入新语义
    op.add_column(
        "t_agent_run",
        sa.Column(
            "claim_epoch",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="认领代次：每次认领 +1，单调递增永不归零——checkpoint/终态写的 fencing 条件，拒绝僵尸 worker 的迟到写",
        ),
    )
    op.add_column(
        "t_agent_run",
        sa.Column(
            "heartbeat_at",
            sa.BigInteger(),
            nullable=True,
            comment="持有 worker 的业务心跳（间隔 lease_ttl/3）；僵尸判定依据。不复用 updated_at——通用审计戳会被任何写碰",
        ),
    )


def downgrade() -> None:
    op.drop_column("t_agent_run", "heartbeat_at")
    op.drop_column("t_agent_run", "claim_epoch")
