"""add per-user custom LLM providers and models

Revision ID: 202608220003
Revises: 202608220002
Create Date: 2026-08-22
"""

from alembic import op
import sqlalchemy as sa


revision = "202608220003"
down_revision = "202608220002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_llm_providers",
        sa.Column("id", sa.String(36), primary_key=True, comment="Provider ID"),
        sa.Column("user_id", sa.Integer(), nullable=False, comment="用户 ID"),
        sa.Column("name", sa.String(120), nullable=False, comment="显示名"),
        sa.Column("api_type", sa.String(32), nullable=False, comment="协议类型"),
        sa.Column("base_url", sa.String(500), nullable=False, comment="OpenAI 兼容端点"),
        sa.Column("api_key_cipher", sa.Text(), nullable=True, comment="加密后的 API Key"),
        sa.Column("api_key_suffix", sa.String(16), nullable=True, comment="Key 尾部明文片段"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("deleted_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        comment="用户自定义模型服务连接配置",
    )
    op.create_index("idx_user_llm_providers_user", "user_llm_providers", ["user_id"])

    op.create_table(
        "user_llm_models",
        sa.Column("id", sa.String(36), primary_key=True, comment="条目 ID"),
        sa.Column("user_id", sa.Integer(), nullable=False, comment="用户 ID"),
        sa.Column("provider_id", sa.String(36), sa.ForeignKey("user_llm_providers.id"), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False, comment="发送给端点的模型 ID"),
        sa.Column("label", sa.String(200), nullable=False, comment="选择器显示名"),
        sa.Column("temperature", sa.Float(), nullable=True, comment="可选温度覆盖"),
        sa.Column("context_window", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        comment="用户自定义对话模型条目",
    )
    op.create_index("idx_user_llm_models_user", "user_llm_models", ["user_id"])
    op.create_index(
        "uq_user_llm_models_user_model", "user_llm_models", ["user_id", "model_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_user_llm_models_user_model", table_name="user_llm_models")
    op.drop_index("idx_user_llm_models_user", table_name="user_llm_models")
    op.drop_table("user_llm_models")
    op.drop_index("idx_user_llm_providers_user", table_name="user_llm_providers")
    op.drop_table("user_llm_providers")
