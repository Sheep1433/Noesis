from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from noesis.services.memory.index import MemoryIndexService, index_document


class Embedding:
    def embed_query(self, _text):
        return [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_active_item_upserts_with_authoritative_scope_payload(monkeypatch) -> None:
    item = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        user_id="user-1",
        scope_key="profile:SUPER_AGENT_QA|project:global",
        memory_type="decision",
        subject="Memory switch",
        statement="Use one switch.",
        applicability="Machine memory",
        effective_provenance="user",
        status="active",
        version=1,
        valid_from=datetime(2026, 8, 24, tzinfo=timezone.utc),
        valid_to=None,
    )

    class Repository:
        def __init__(self, _db):
            pass

        async def get_item(self, *_args, **_kwargs):
            return item

    monkeypatch.setattr("noesis.services.memory.index.MachineMemoryRepository", Repository)
    client = MagicMock()
    client.collection_exists.return_value = False
    service = MemoryIndexService(client=client, embedding=Embedding())

    outcome = await service.sync_item(
        SimpleNamespace(),
        user_id="user-1",
        scope_key=item.scope_key,
        memory_id=item.id,
    )

    assert outcome == "upserted"
    client.create_collection.assert_called_once()
    point = client.upsert.call_args_list[-1].kwargs["points"][0]
    assert point.id == item.id
    assert point.payload["user_id"] == "user-1"
    assert point.payload["scope_key"] == item.scope_key
    assert "provider" not in point.payload
    assert "Memory switch" in index_document(item)


@pytest.mark.asyncio
async def test_late_event_deletes_missing_or_nonactive_item(monkeypatch) -> None:
    class Repository:
        def __init__(self, _db):
            pass

        async def get_item(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr("noesis.services.memory.index.MachineMemoryRepository", Repository)
    client = MagicMock()
    client.collection_exists.return_value = True
    service = MemoryIndexService(client=client, embedding=Embedding())

    outcome = await service.sync_item(
        SimpleNamespace(),
        user_id="user-1",
        scope_key="scope",
        memory_id="00000000-0000-0000-0000-000000000001",
    )

    assert outcome == "deleted"
    client.delete.assert_called_once()
    client.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_account_cleanup_deletes_all_user_points_without_embedding() -> None:
    client = MagicMock()
    client.collection_exists.return_value = True
    service = MemoryIndexService(client=client)

    await service.delete_user("user-1")

    selector = client.delete.call_args.kwargs["points_selector"]
    assert selector.filter.must[0].key == "user_id"
    assert selector.filter.must[0].match.value == "user-1"


@pytest.mark.asyncio
async def test_template_or_dimension_change_rebuilds_collection(monkeypatch) -> None:
    item = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        user_id="user-1",
        scope_key="scope",
        memory_type="decision",
        subject="Switch",
        statement="Use one switch.",
        applicability="Current repository",
        effective_provenance="user",
        status="active",
        version=1,
        valid_from=datetime.now(timezone.utc),
        valid_to=None,
    )

    class Repository:
        def __init__(self, _db):
            pass

        async def get_item(self, *_args, **_kwargs):
            return item

    monkeypatch.setattr("noesis.services.memory.index.MachineMemoryRepository", Repository)
    client = MagicMock()
    client.collection_exists.return_value = True
    client.get_collection.return_value = SimpleNamespace(
        config=SimpleNamespace(params=SimpleNamespace(vectors=SimpleNamespace(size=2)))
    )
    client.retrieve.return_value = []
    service = MemoryIndexService(client=client, embedding=Embedding())
    service.rebuild = AsyncMock(return_value=1)
    db = SimpleNamespace()

    outcome = await service.sync_item(
        db, user_id="user-1", scope_key="scope", memory_id=item.id
    )

    assert outcome == "rebuilt"
    service.rebuild.assert_awaited_once_with(db, vector_size=3)
