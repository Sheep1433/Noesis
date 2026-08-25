"""user_llm_models unique key scoped by provider (composite model identity)

Revision ID: 202608250002
Revises: 202608250001
Create Date: 2026-08-25
"""

from alembic import op

revision = "202608250002"
down_revision = "202608250001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 模型身份改为「provider + model_id」复合：同名模型可分属不同 Provider，
    # 也可与内置目录同名（选择器身份为 slug/model_id，不再冲突）
    op.drop_index("uq_user_llm_models_user_model", table_name="user_llm_models")
    op.create_index(
        "uq_user_llm_models_user_provider_model",
        "user_llm_models",
        ["user_id", "provider_id", "model_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_user_llm_models_user_provider_model", table_name="user_llm_models"
    )
    op.create_index(
        "uq_user_llm_models_user_model",
        "user_llm_models",
        ["user_id", "model_id"],
        unique=True,
    )
