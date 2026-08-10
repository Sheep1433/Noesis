"""store global registration invite on admin user

Revision ID: 202607130003
Revises: 202607130002
Create Date: 2026-07-13
"""

from alembic import op
import sqlalchemy as sa

revision = "202607130003"
down_revision = "202607130002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS t_registration_invite")
    op.add_column("t_user", sa.Column("registration_invite_digest", sa.String(64), nullable=True, comment="当前全局注册码 SHA-256 摘要"))
    op.add_column("t_user", sa.Column("registration_invite_updated_at", sa.BigInteger(), nullable=True, comment="当前全局注册码轮换时间（毫秒）"))
    op.create_unique_constraint("uq_t_user_registration_invite_digest", "t_user", ["registration_invite_digest"])


def downgrade() -> None:
    op.drop_constraint("uq_t_user_registration_invite_digest", "t_user", type_="unique")
    op.drop_column("t_user", "registration_invite_updated_at")
    op.drop_column("t_user", "registration_invite_digest")
