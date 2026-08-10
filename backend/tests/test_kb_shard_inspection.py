from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient, models

from noesis.knowledge.implementations.qdrant import QdrantService
from noesis.knowledge.base import KBNotFoundError, KBOperationError
from noesis.knowledge.retrieval.store import VectorStore
from noesis.errors.exceptions import NotFoundException
from noesis.schemas.knowledge_base_schema import ShardPageQuery
from noesis.services import knowledge_base_service
from server.api.knowledge_base_api import knowledge_base_router
from server.auth_dependencies import get_current_user
from server.exception_handlers import handle_exception
from fastapi import FastAPI


def _memory_service() -> QdrantService:
    client = QdrantClient(":memory:")
    client.create_collection(
        "docs",
        vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE),
    )
    client.upsert(
        "docs",
        [
            models.PointStruct(
                id=index + 1,
                vector=[0.1, 0.2],
                payload={
                    "file_name": "guide.md" if index < 5 else "other.md",
                    "content": f"第 {index} 段 登录说明" if index % 2 == 0 else f"第 {index} 段 配置说明",
                    "chunk_index": index,
                    "element_type": "table" if index == 2 else "text",
                    "locator": {"type": "page", "page_start": index + 1, "page_end": index + 1},
                    "header_path": f"章节 > {index}",
                    "token_count": 10 + index,
                },
            )
            for index in range(6)
        ],
    )
    return QdrantService(client)


def test_shard_page_query_rejects_unsupported_values() -> None:
    with pytest.raises(ValueError):
        ShardPageQuery(limit=101)
    with pytest.raises(ValueError):
        ShardPageQuery(element_type="audio")
    with pytest.raises(ValueError):
        ShardPageQuery(locator_type="sheet")


def test_qdrant_shard_page_is_stable_and_cursor_has_no_duplicates() -> None:
    service = _memory_service()

    first = service.get_document_shards_page("docs", "guide.md", ShardPageQuery(limit=2))
    second = service.get_document_shards_page(
        "docs", "guide.md", ShardPageQuery(limit=2, cursor=first["next_cursor"])
    )
    third = service.get_document_shards_page(
        "docs", "guide.md", ShardPageQuery(limit=2, cursor=second["next_cursor"])
    )

    assert first["total"] == 5
    assert [item["chunk_index"] for item in first["items"]] == [0, 1]
    assert [item["chunk_index"] for item in second["items"]] == [2, 3]
    assert [item["chunk_index"] for item in third["items"]] == [4]
    assert third["next_cursor"] is None
    assert len({item["id"] for page in (first, second, third) for item in page["items"]}) == 5
    assert "content" not in first["items"][0]
    assert first["items"][0]["content_preview"].startswith("第 0 段")


def test_qdrant_shard_page_filters_keyword_type_and_locator() -> None:
    service = _memory_service()

    keyword = service.get_document_shards_page(
        "docs", "guide.md", ShardPageQuery(keyword="登录")
    )
    table = service.get_document_shards_page(
        "docs", "guide.md", ShardPageQuery(element_type="table", locator_type="page")
    )

    assert [item["chunk_index"] for item in keyword["items"]] == [0, 2, 4]
    assert table["total"] == 1
    assert table["items"][0]["chunk_index"] == 2
    assert service.get_document_shards_page(
        "docs", "guide.md", ShardPageQuery(keyword="不存在的内容")
    )["total"] == 0

    with pytest.raises(KBNotFoundError, match="missing.md"):
        service.get_document_shards_page("docs", "missing.md", ShardPageQuery())


def test_qdrant_shard_page_keeps_duplicate_indexes_in_point_id_order() -> None:
    service = _memory_service()
    service.client.upsert(
        "docs",
        [
            models.PointStruct(
                id=point_id,
                vector=[0.1, 0.2],
                payload={
                    "file_name": "duplicates.md",
                    "content": f"重复序号 {point_id}",
                    "chunk_index": chunk_index,
                },
            )
            for point_id, chunk_index in ((9, 0), (7, 0), (8, 0), (10, 1))
        ],
    )

    first = service.get_document_shards_page(
        "docs", "duplicates.md", ShardPageQuery(limit=2)
    )
    second = service.get_document_shards_page(
        "docs",
        "duplicates.md",
        ShardPageQuery(limit=2, cursor=first["next_cursor"]),
    )

    assert [item["id"] for item in first["items"]] == ["7", "8"]
    assert [item["id"] for item in second["items"]] == ["9", "10"]
    assert second["next_cursor"] is None


@pytest.mark.parametrize(
    ("sort", "expected_ids"),
    [("asc", ["22", "20", "21", "23"]), ("desc", ["20", "22", "21", "23"])],
)
def test_qdrant_shard_page_puts_missing_indexes_last(
    sort: str, expected_ids: list[str]
) -> None:
    service = _memory_service()
    service.client.upsert(
        "docs",
        [
            models.PointStruct(
                id=20,
                vector=[0.1, 0.2],
                payload={"file_name": "legacy.md", "content": "有序 1", "chunk_index": 1},
            ),
            models.PointStruct(
                id=21,
                vector=[0.1, 0.2],
                payload={"file_name": "legacy.md", "content": "旧分片 A"},
            ),
            models.PointStruct(
                id=22,
                vector=[0.1, 0.2],
                payload={"file_name": "legacy.md", "content": "有序 0", "chunk_index": 0},
            ),
            models.PointStruct(
                id=23,
                vector=[0.1, 0.2],
                payload={"file_name": "legacy.md", "content": "旧分片 B"},
            ),
        ],
    )

    ids: list[str] = []
    cursor = None
    while True:
        page = service.get_document_shards_page(
            "docs",
            "legacy.md",
            ShardPageQuery(limit=1, cursor=cursor, sort=sort),
        )
        ids.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert ids == expected_ids


def test_qdrant_shard_page_rejects_stale_cursor_and_index_failure() -> None:
    service = _memory_service()
    first = service.get_document_shards_page("docs", "guide.md", ShardPageQuery(limit=1))

    with pytest.raises(ValueError, match="列表状态已失效"):
        service.get_document_shards_page(
            "docs",
            "guide.md",
            ShardPageQuery(limit=1, cursor=first["next_cursor"], sort="desc"),
        )

    client = MagicMock()
    client.create_payload_index.side_effect = RuntimeError("index unavailable")
    with pytest.raises(KBOperationError, match="筛选暂不可用"):
        QdrantService(client).get_document_shards_page(
            "docs", "guide.md", ShardPageQuery()
        )
    client.scroll.assert_not_called()


def test_create_collection_rolls_back_when_inspection_indexes_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = QdrantClient(":memory:")
    service = QdrantService(client)
    monkeypatch.setattr(
        service,
        "_ensure_inspection_indexes",
        MagicMock(side_effect=RuntimeError("index unavailable")),
    )

    result = service.create_collection("incomplete", vector_dimension=2)

    assert result["success"] is False
    assert client.collection_exists("incomplete") is False


def test_shard_detail_does_not_request_vector_and_maps_controlled_metadata() -> None:
    client = MagicMock()
    client.retrieve.return_value = [
        SimpleNamespace(
            id="point-1",
            payload={
                "content": "正文",
                "file_name": "guide.md",
                "element_type": "text",
                "locator": {"type": "page", "page_start": 3, "page_end": 3},
                "document_id": "doc_1",
                "document_version_id": "docv_1",
                "segment_id": "seg_1",
                "metadata": {"page_no": 3, "secret": "must-not-leak"},
            },
        )
    ]
    info = SimpleNamespace(
        config=SimpleNamespace(params=SimpleNamespace(vectors=SimpleNamespace(size=1024)))
    )
    client.get_collection.return_value = info

    result = QdrantService(client).get_shard_detail("docs", "point-1")

    assert result is not None
    assert result["vector_dimension"] == 1024
    assert result["locator"]["page_start"] == 3
    assert result["document_id"] == "doc_1"
    assert result["raw_metadata"] == {"page_no": 3}
    client.retrieve.assert_called_once_with(
        collection_name="docs", ids=["point-1"], with_payload=True, with_vectors=False
    )


def test_shard_detail_does_not_turn_qdrant_failure_into_not_found() -> None:
    client = MagicMock()
    client.retrieve.side_effect = RuntimeError("qdrant unavailable")

    with pytest.raises(KBOperationError, match="详情加载失败"):
        QdrantService(client).get_shard_detail("docs", "point-1")


def test_bm25_loaded_documents_keep_qdrant_point_id() -> None:
    client = QdrantClient(":memory:")
    client.create_collection(
        "docs",
        vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE),
    )
    client.upsert(
        "docs",
        [
            models.PointStruct(
                id=42,
                vector=[0.1, 0.2],
                payload={
                    "file_name": "guide.md",
                    "page_content": "登录说明",
                    "content_hash": "content-hash-is-not-the-point-id",
                },
            )
        ],
    )
    store = VectorStore.__new__(VectorStore)
    store.client = client
    store.collection_name = "docs"

    documents = store.load_all_documents()

    assert documents[0].metadata["point_id"] == "42"


def test_shard_page_api_invalid_limit_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    handle_exception(app)
    app.include_router(knowledge_base_router)
    app.dependency_overrides[get_current_user] = lambda: MagicMock()
    monkeypatch.setattr(knowledge_base_service, "get_shards", MagicMock())

    response = TestClient(app).get(
        "/api/knowledge_base/collections/docs/documents/guide.md/shards",
        params={"limit": "101"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == 400


def test_shard_detail_api_returns_404_business_code(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    handle_exception(app)
    app.include_router(knowledge_base_router)
    app.dependency_overrides[get_current_user] = lambda: MagicMock()
    monkeypatch.setattr(
        knowledge_base_service,
        "get_shard_detail",
        AsyncMock(side_effect=NotFoundException(message="分片不存在")),
    )

    response = TestClient(app).get(
        "/api/knowledge_base/collections/docs/shards/missing"
    )

    assert response.status_code == 404
    assert response.json()["code"] == 404


def test_shard_page_api_invalid_cursor_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    handle_exception(app)
    app.include_router(knowledge_base_router)
    app.dependency_overrides[get_current_user] = lambda: MagicMock()
    monkeypatch.setattr(
        knowledge_base_service,
        "get_shards",
        AsyncMock(side_effect=ValueError("列表状态已失效，请重新加载")),
    )

    response = TestClient(app).get(
        "/api/knowledge_base/collections/docs/documents/guide.md/shards",
        params={"cursor": "invalid"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == 400


def test_shard_page_api_missing_document_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    handle_exception(app)
    app.include_router(knowledge_base_router)
    app.dependency_overrides[get_current_user] = lambda: MagicMock()
    monkeypatch.setattr(
        knowledge_base_service,
        "get_shards",
        AsyncMock(side_effect=NotFoundException(message="文档不存在")),
    )

    response = TestClient(app).get(
        "/api/knowledge_base/collections/docs/documents/missing.md/shards"
    )

    assert response.status_code == 404
    assert response.json()["code"] == 404
