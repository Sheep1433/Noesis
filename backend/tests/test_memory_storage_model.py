from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, insert, update
from sqlalchemy.exc import IntegrityError

import noesis.schemas.memory as memory_schemas
from noesis.schemas.memory import StrictMemoryModel
from noesis.storage.postgres.models.memory import (
    TMemoryEvidence,
    TMemoryItem,
    TMemoryJob,
    TMemoryOutbox,
    TMemoryQueryTrace,
    TMemoryRelation,
    TMemoryRunSnapshot,
    TMemoryUserPreference,
)


def _item(item_id: str, *, status: str = "candidate") -> dict[str, object]:
    return {
        "id": item_id,
        "user_id": "user-1",
        "scope_key": "SUPER_AGENT_QA:repo:fixture",
        "memory_type": "decision",
        "subject": "memory switch",
        "subject_key": "memory-switch",
        "statement": "Use one switch.",
        "applicability": "All machine-memory automation.",
        "content_digest": "a" * 64,
        "effective_provenance": "user",
        "status": status,
        "version": 1,
    }


def test_memory_schema_contains_only_new_authoritative_tables() -> None:
    assert {
        TMemoryUserPreference.__tablename__,
        TMemoryRunSnapshot.__tablename__,
        TMemoryItem.__tablename__,
        TMemoryEvidence.__tablename__,
        TMemoryRelation.__tablename__,
        TMemoryJob.__tablename__,
        TMemoryOutbox.__tablename__,
        TMemoryQueryTrace.__tablename__,
    } == {
        "t_memory_user_preference",
        "t_memory_run_snapshot",
        "t_memory_item",
        "t_memory_evidence",
        "t_memory_relation",
        "t_memory_job",
        "t_memory_outbox",
        "t_memory_query_trace",
    }
    assert not hasattr(TMemoryItem, "failure_summary")
    assert not hasattr(TMemoryItem, "repair_action")


def test_one_current_item_per_identity_and_historical_versions_allowed() -> None:
    engine = create_engine("sqlite://")
    TMemoryItem.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(insert(TMemoryItem), _item("item-1"))
        with pytest.raises(IntegrityError):
            connection.execute(insert(TMemoryItem), _item("item-2"))

    with engine.begin() as connection:
        connection.execute(
            update(TMemoryItem)
            .where(TMemoryItem.id == "item-1")
            .values(
                status="superseded", valid_to=datetime(2026, 8, 24, tzinfo=timezone.utc)
            )
        )
        connection.execute(insert(TMemoryItem), _item("item-2"))

    assert {index.name for index in TMemoryItem.__table__.indexes} >= {
        "uq_memory_item_current_identity",
        "uq_memory_item_supersedes",
    }


def test_memory_migration_chain_has_one_head_after_schema_reset() -> None:
    root = (
        Path(__file__).parents[1]
        / "packages"
        / "noesis-core"
        / "src"
        / "noesis"
        / "storage"
        / "migrations"
    )
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root))
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == ["202608240003"]
    reset = scripts.get_revision("202608240001")
    assert reset is not None
    assert reset.down_revision == "202608220003"


def test_memory_schema_fields_have_descriptions() -> None:
    models = [
        value
        for value in vars(memory_schemas).values()
        if isinstance(value, type)
        and issubclass(value, StrictMemoryModel)
        and value is not StrictMemoryModel
    ]
    missing = {
        f"{model.__name__}.{name}"
        for model in models
        for name, field in model.model_fields.items()
        if not field.description
    }
    assert not missing


def test_empty_database_upgrade_and_rollback_sql_use_only_new_schema() -> None:
    root = (
        Path(__file__).parents[1]
        / "packages"
        / "noesis-core"
        / "src"
        / "noesis"
        / "storage"
        / "migrations"
    )
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root))
    upgrade_sql = StringIO()
    config.output_buffer = upgrade_sql
    command.upgrade(config, "head", sql=True)
    rendered_upgrade = upgrade_sql.getvalue()

    assert "CREATE TABLE t_memory_run_snapshot" in rendered_upgrade
    assert "CREATE TABLE t_memory_item" in rendered_upgrade
    assert "CREATE TABLE t_memory_job" in rendered_upgrade
    assert "CREATE TABLE t_memory_query_trace" in rendered_upgrade
    assert "t_memory_extraction_job" not in rendered_upgrade
    assert "t_memory_index_outbox" not in rendered_upgrade

    downgrade_sql = StringIO()
    config.output_buffer = downgrade_sql
    command.downgrade(config, "head:base", sql=True)
    rendered_downgrade = downgrade_sql.getvalue()
    assert "DROP TABLE t_memory_item" in rendered_downgrade
    assert "DROP TABLE t_memory_user_preference" in rendered_downgrade
