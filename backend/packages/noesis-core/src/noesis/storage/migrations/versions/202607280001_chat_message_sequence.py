"""deterministic chat message sequence

Revision ID: 202607280001
Revises: 202607270002
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa


revision = "202607280001"
down_revision = "202607270002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "t_chat_session",
        sa.Column("next_message_sequence", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column(
        "t_chat_message",
        sa.Column("message_sequence", sa.BigInteger(), nullable=True),
    )

    # Parent-aware deterministic backfill. UUID is only the final tie-breaker;
    # an assistant sharing a timestamp with its parent is always ranked after user rows.
    op.execute(
        """
        WITH ranked AS (
            SELECT
                m.id,
                row_number() OVER (
                    PARTITION BY m.session_id
                    ORDER BY
                        CASE
                            WHEN m.role = 'assistant' AND p.id IS NOT NULL
                                THEN GREATEST(m.created_at, p.created_at)
                            ELSE m.created_at
                        END,
                        CASE WHEN m.role = 'user' THEN 0 ELSE 1 END,
                        m.id
                ) AS seq
            FROM t_chat_message AS m
            LEFT JOIN t_chat_message AS p ON p.id = m.parent_id
        )
        UPDATE t_chat_message AS m
        SET message_sequence = ranked.seq
        FROM ranked
        WHERE m.id = ranked.id
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM t_chat_message
                WHERE message_sequence IS NULL
            ) THEN
                RAISE EXCEPTION 'message_sequence backfill left null rows';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM t_chat_message
                GROUP BY session_id, message_sequence
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'message_sequence backfill produced duplicates';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM t_chat_message AS child
                JOIN t_chat_message AS parent ON parent.id = child.parent_id
                WHERE child.role = 'assistant'
                  AND child.message_sequence <= parent.message_sequence
            ) THEN
                RAISE EXCEPTION 'assistant message_sequence is not after parent';
            END IF;
        END $$
        """
    )
    op.alter_column("t_chat_message", "message_sequence", nullable=False)
    op.create_unique_constraint(
        "uq_chat_message_session_sequence",
        "t_chat_message",
        ["session_id", "message_sequence"],
    )
    op.drop_index("idx_message_session", table_name="t_chat_message")
    op.create_index(
        "idx_message_session",
        "t_chat_message",
        ["session_id", "message_sequence"],
    )
    op.execute(
        """
        UPDATE t_chat_session AS s
        SET next_message_sequence = COALESCE(
            (SELECT max(m.message_sequence) + 1 FROM t_chat_message AS m WHERE m.session_id = s.id),
            1
        )
        """
    )


def downgrade() -> None:
    op.drop_index("idx_message_session", table_name="t_chat_message")
    op.create_index("idx_message_session", "t_chat_message", ["session_id", "created_at"])
    op.drop_constraint("uq_chat_message_session_sequence", "t_chat_message", type_="unique")
    op.drop_column("t_chat_message", "message_sequence")
    op.drop_column("t_chat_session", "next_message_sequence")
