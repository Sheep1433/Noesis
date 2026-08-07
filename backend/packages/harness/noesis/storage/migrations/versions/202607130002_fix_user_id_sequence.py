"""synchronize user id sequence after seeded admin

Revision ID: 202607130002
Revises: 202607130001
Create Date: 2026-07-13
"""

from alembic import op

revision = "202607130002"
down_revision = "202607130001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "SELECT setval(pg_get_serial_sequence('t_user', 'id'), "
        "COALESCE((SELECT MAX(id) FROM t_user), 1), true)"
    )


def downgrade() -> None:
    pass
