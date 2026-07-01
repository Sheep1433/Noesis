"""KbCollectionConfigService 单测（mock DB / Qdrant）。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.kb_collection_config_service import KbCollectionConfigService


@pytest.mark.asyncio
async def test_create_default_idempotent():
    db = AsyncMock()
    existing = MagicMock()
    with patch.object(
        KbCollectionConfigService, "get_row", new=AsyncMock(return_value=existing)
    ):
        row = await KbCollectionConfigService.create_default(db, "my_col")
    assert row is existing
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_patch_config_merges_processing():
    db = AsyncMock()
    row = MagicMock()
    row.processing_params = {"chunk_parser_config": {"chunk_size": 500}}
    row.query_params = {}
    with patch.object(KbCollectionConfigService, "get_row", new=AsyncMock(return_value=row)):
        with patch.object(
            KbCollectionConfigService,
            "get_config",
            new=AsyncMock(return_value={"collection_name": "c", "processing_params": {}, "query_params": {}}),
        ):
            out = await KbCollectionConfigService.patch_config(
                db,
                "c",
                processing_params={"chunk_parser_config": {"chunk_size": 800}},
            )
    assert out is not None
    assert row.processing_params["chunk_parser_config"]["chunk_size"] == 800


@pytest.mark.asyncio
async def test_ensure_defaults_for_qdrant_collections():
    db = AsyncMock()
    with patch("services.kb_collection_config_service.is_qdrant_connected", return_value=True):
        with patch.object(
            KbCollectionConfigService, "get_row", new=AsyncMock(return_value=None)
        ):
            with patch.object(
                KbCollectionConfigService, "create_default", new=AsyncMock()
            ) as mock_create:
                svc = MagicMock()
                svc.get_collections.return_value = [{"name": "new_col"}]
                with patch("services.kb_collection_config_service.QdrantService", return_value=svc):
                    n = await KbCollectionConfigService.ensure_defaults_for_qdrant_collections(db)
    assert n == 1
    mock_create.assert_awaited_once()
