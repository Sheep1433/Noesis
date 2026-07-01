"""kb_collection_config 集合配置表

Revision ID: 202606300001
Revises: 202606290001
Create Date: 2026-06-30

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "202606300001"
down_revision: Union[str, Sequence[str], None] = "202606290001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kb_collection_config",
        sa.Column(
            "collection_name",
            sa.String(length=255),
            nullable=False,
            comment="与 Qdrant collection 同名",
        ),
        sa.Column("processing_params", sa.JSON(), nullable=True, comment="入库默认参数"),
        sa.Column("query_params", sa.JSON(), nullable=True, comment="检索默认参数"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="更新时间",
        ),
        sa.PrimaryKeyConstraint("collection_name"),
        comment="知识库集合配置",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )


def downgrade() -> None:
    op.drop_table("kb_collection_config")
