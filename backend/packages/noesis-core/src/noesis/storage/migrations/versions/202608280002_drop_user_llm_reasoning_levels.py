"""drop reasoning_levels capability column from user_llm_models

推理档位改为聊天界面常显的纯请求参数，per-model 能力声明链整体移除
（含设置页声明入口与 API 字段）；本列随之废弃。

Revision ID: 202608280002
Revises: 202608280001
Create Date: 2026-08-28
"""

from alembic import op
import sqlalchemy as sa

revision = "202608280002"
down_revision = "202608280001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("user_llm_models", "reasoning_levels")


def downgrade() -> None:
    op.add_column(
        "user_llm_models",
        sa.Column(
            "reasoning_levels",
            sa.String(64),
            nullable=True,
            comment="推理档位能力声明（逗号分隔子集 off,low,medium,high,max；NULL=未声明不显示控件）",
        ),
    )
