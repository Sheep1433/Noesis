"""t_agent_run_command table（跨进程 durable command）

Revision ID: 202609190002
Revises: 202609190001
Create Date: 2026-09-19
"""

from alembic import op
import sqlalchemy as sa

revision = "202609190002"
down_revision = "202609190001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "t_agent_run_command",
        sa.Column("id", sa.String(length=36), primary_key=True, comment="命令 ID"),
        sa.Column("run_id", sa.String(length=64), nullable=True, comment="目标 Run（bg_task_stop 可为空）"),
        sa.Column("task_id", sa.String(length=64), nullable=True, comment="后台任务 ID（bg_task_stop）"),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False, comment="stop | hitl_resume | bg_task_stop"),
        sa.Column("dedupe_key", sa.String(length=200), nullable=False, comment="幂等去重键"),
        sa.Column("decision_digest", sa.String(length=64), nullable=True, comment="HITL 决策摘要（sha256）"),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, comment="pending | claimed | completed | rejected | no_op"),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("claimed_at", sa.BigInteger(), nullable=True),
        sa.Column("completed_at", sa.BigInteger(), nullable=True),
        sa.UniqueConstraint("user_id", "dedupe_key", name="uq_agent_run_command_dedupe"),
    )
    op.create_index("idx_agent_run_command_pending", "t_agent_run_command", ["status", "created_at"])
    op.create_index("idx_agent_run_command_run", "t_agent_run_command", ["run_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_agent_run_command_run", table_name="t_agent_run_command")
    op.drop_index("idx_agent_run_command_pending", table_name="t_agent_run_command")
    op.drop_table("t_agent_run_command")
