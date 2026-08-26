"""replace the unreleased memory prototype schema

Revision ID: 202608240001
Revises: 202608220003
Create Date: 2026-08-24
"""

from __future__ import annotations

import importlib

from alembic import op
import sqlalchemy as sa


revision = "202608240001"
down_revision = "202608220003"
branch_labels = None
depends_on = None

_OLD_TABLES = (
    "t_memory_evidence",
    "t_memory_extraction_job",
    "t_memory_index_outbox",
    "t_memory_item",
)
_NEW_TABLES = (
    "t_memory_outbox",
    "t_memory_job",
    "t_memory_relation",
    "t_memory_evidence",
    "t_memory_item",
    "t_memory_run_snapshot",
    "t_memory_query_trace",
)


def _schema_module():
    return importlib.import_module(
        "noesis.storage.migrations.versions.202608220001_machine_memory"
    )


def upgrade() -> None:
    if op.get_context().as_sql:
        return
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    item_columns = (
        {column["name"] for column in inspector.get_columns("t_memory_item")}
        if "t_memory_item" in tables
        else set()
    )
    if "statement" in item_columns and set(_NEW_TABLES) <= tables:
        return

    for table in _OLD_TABLES:
        if table in tables:
            op.drop_table(table)
    _schema_module()._create_schema(include_preference=False)


def downgrade() -> None:
    # The discarded prototype is intentionally never restored. A full downgrade
    # continues to the original schema revision, which drops the new tables once.
    return
