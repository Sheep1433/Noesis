"""bg_shell_job 加 output_tail（后台命令运行中输出尾部快照）

Revision ID: 202609230001
Revises: 202609220001
Create Date: 2026-09-23
"""

from alembic import op
import sqlalchemy as sa

revision = "202609230001"
down_revision = "202609220001"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column(
        "bg_shell_job",
        sa.Column("output_tail", sa.Text(), nullable=True, comment="运行中输出尾部快照（流式 flush，终态后为 None）"),
    )

def downgrade() -> None:
    op.drop_column("bg_shell_job", "output_tail")
