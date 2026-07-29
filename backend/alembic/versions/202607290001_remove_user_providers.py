"""remove user-managed providers and model bindings

Revision ID: 202607290001
Revises: 202607280001
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa

revision = "202607290001"
down_revision = "202607280001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("idx_user_model_purpose_bindings_provider", table_name="user_model_purpose_bindings")
    op.drop_table("user_model_purpose_bindings")
    op.drop_index("idx_user_provider_connections_user", table_name="user_provider_connections")
    op.drop_table("user_provider_connections")


def downgrade() -> None:
    op.create_table(
        "user_provider_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider_type", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("secret_ciphertext", sa.Text(), nullable=True),
        sa.Column("secret_suffix", sa.String(16), nullable=True),
        sa.Column("secret_updated_at", sa.BigInteger(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("user_id", "display_name", name="uq_user_provider_connections_name"),
    )
    op.create_index("idx_user_provider_connections_user", "user_provider_connections", ["user_id"])
    op.create_table(
        "user_model_purpose_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("provider_id", sa.String(36), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("user_id", "purpose", name="uq_user_model_purpose_binding"),
    )
    op.create_index("idx_user_model_purpose_bindings_provider", "user_model_purpose_bindings", ["user_id", "provider_id"])
