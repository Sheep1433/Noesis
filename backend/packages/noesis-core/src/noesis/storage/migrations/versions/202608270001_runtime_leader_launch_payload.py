"""Runtime leader term + run launch payload / owner term (enable-distributed-sse-pubsub).

Revision ID: 202608270001
Revises: 202608260003
Create Date: 2026-08-27

P1（memory 共享状态机）：
- t_runtime_leader：单行全局 leadership term，leader 每次获锁原子 +1
- t_agent_run.launch_payload：dispatcher 重建 producer 的 schema 化启动载荷（无认证秘密）
- t_agent_run.owner_term：claim 时的 leader term（0=未 claim），旧 term 迟到写入拒绝
"""

import sqlalchemy as sa
from alembic import op

revision = "202608270001"
down_revision = "202608260003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "t_runtime_leader",
        sa.Column("cluster_id", sa.String(100), primary_key=True),
        sa.Column("leader_term", sa.BigInteger(), nullable=False),
        sa.Column("instance_id", sa.String(200), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("cluster_id"),
    )
    op.create_index("idx_runtime_leader_updated", "t_runtime_leader", ["updated_at"])
    op.add_column(
        "t_agent_run",
        sa.Column(
            "owner_term",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="claim 时的 leader term；0=未被任何 leader claim",
        ),
    )
    op.add_column(
        "t_agent_run",
        sa.Column(
            "launch_payload",
            sa.JSON(),
            nullable=True,
            comment="dispatcher 重建 producer 所需的 schema 化启动载荷（无认证秘密）",
        ),
    )


def downgrade() -> None:
    op.drop_column("t_agent_run", "launch_payload")
    op.drop_column("t_agent_run", "owner_term")
    op.drop_index("idx_runtime_leader_updated", table_name="t_runtime_leader")
    op.drop_table("t_runtime_leader")
