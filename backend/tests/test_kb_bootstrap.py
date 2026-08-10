from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from noesis.services.kb_collection_config_service import KbCollectionConfigService
from server.bootstrap.kb import sync_existing_kb_collection_configs


@asynccontextmanager
async def _session_context(db: MagicMock):
    yield db


async def _run_startup_sync(collection_names: list[str]):
    service = MagicMock()
    service.client = MagicMock()
    service.get_collections.return_value = [
        {"name": name} for name in collection_names
    ]
    runtime = MagicMock()
    runtime.connected = True
    runtime.service.return_value = service

    db = MagicMock()
    db.commit = AsyncMock()
    repo = MagicMock()
    repo.get_row = AsyncMock(return_value=None)
    repo.create_default = AsyncMock()

    with (
        patch("server.bootstrap.kb.knowledge_base", runtime),
        patch("noesis.knowledge.runtime.knowledge_base", runtime),
        patch.object(KbCollectionConfigService, "_repo", return_value=repo),
        patch(
            "noesis.storage.postgres.manager.pg_manager.get_async_session_context",
            return_value=_session_context(db),
        ),
    ):
        await sync_existing_kb_collection_configs()

    return service, repo, db


@pytest.mark.asyncio
async def test_empty_qdrant_does_not_create_default_collections() -> None:
    service, repo, db = await _run_startup_sync([])

    service.create_collection.assert_not_called()
    service.delete_collection.assert_not_called()
    repo.create_default.assert_not_awaited()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_historical_default_collections_are_kept_and_configured() -> None:
    service, repo, db = await _run_startup_sync(
        ["requirement_docs", "test_case_docs"]
    )

    service.create_collection.assert_not_called()
    service.delete_collection.assert_not_called()
    assert repo.create_default.await_args_list == [
        call("requirement_docs"),
        call("test_case_docs"),
    ]
    db.commit.assert_awaited_once()
