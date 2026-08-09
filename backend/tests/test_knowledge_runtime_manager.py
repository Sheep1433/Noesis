from unittest.mock import MagicMock, patch

import pytest

from noesis.knowledge.implementations.qdrant import QdrantService
from noesis.knowledge.manager import KnowledgeBaseManager
from noesis.knowledge.factory import KnowledgeBaseFactory


@pytest.mark.asyncio
async def test_manager_owns_qdrant_client_lifecycle() -> None:
    client = MagicMock()
    KnowledgeBaseFactory.register(QdrantService)
    manager = KnowledgeBaseManager()

    with patch("noesis.knowledge.manager.QdrantClient", return_value=client):
        assert await manager.initialize() is True

    assert manager.connected is True
    assert manager.client is client
    service = manager.service()
    assert isinstance(service, QdrantService)
    assert service.client is client

    await manager.close()

    client.close.assert_called_once()
    assert manager.connected is False
    assert manager.client is None


@pytest.mark.asyncio
async def test_manager_discards_failed_client() -> None:
    client = MagicMock()
    client.get_collections.side_effect = RuntimeError("unavailable")
    manager = KnowledgeBaseManager()

    with patch("noesis.knowledge.manager.QdrantClient", return_value=client):
        assert await manager.initialize() is False

    client.close.assert_called_once()
    assert manager.connected is False
    assert manager.client is None
