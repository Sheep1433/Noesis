"""KbRetrievalService 单元测试（mock 检索底层）。"""
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document

from noesis.knowledge.retrieval import KbRetrievalService
from noesis.knowledge.runtime import knowledge_base


@pytest.fixture
def mock_retrieval():
    retrieval = MagicMock()
    retrieval.vector_search.return_value = [
        (
            Document(
                page_content="hello",
                metadata={
                    "file_name": "a.md",
                    "header_path": "a.md > Sec",
                    "point_id": "pt-1",
                    "document_id": "doc-1",
                    "document_version_id": "docv-1",
                    "segment_id": "seg-1",
                    "locator": {"type": "header", "path": ["Sec"]},
                },
            ),
            0.9,
        )
    ]
    retrieval.bm25_search.return_value = []
    retrieval.bm25_search_with_scores.return_value = []
    retrieval.hybrid_search.return_value = []
    retrieval.hybrid_search_with_scores.return_value = []
    return retrieval


@patch.object(knowledge_base, "_connected", True)
@patch.object(KbRetrievalService, "_get_retrieval")
def test_search_vector_mode(mock_get_retrieval, mock_retrieval):
    mock_get_retrieval.return_value = mock_retrieval

    result = KbRetrievalService.search(
        collection_name="kb1",
        query="test",
        search_mode="vector",
        limit=3,
        score_threshold=0,
        vector_dimension=1024,
    )

    hits = result.hits
    assert len(hits) == 1
    assert hits[0].search_mode == "vector"
    assert hits[0].file_name == "a.md"
    assert hits[0].header_path == "a.md > Sec"
    assert hits[0].document_id == "doc-1"
    assert hits[0].segment_id == "seg-1"
    assert hits[0].citable is True
    assert result.timing.total_ms >= 0
    assert result.timing.recall_hits == 1
    assert result.timing.final_hits == 1
    mock_retrieval.vector_search.assert_called_once()


@patch.object(knowledge_base, "_connected", True)
@patch.object(KbRetrievalService, "_get_retrieval")
def test_search_with_filters_splits_prefix(mock_get_retrieval, mock_retrieval):
    mock_get_retrieval.return_value = mock_retrieval

    KbRetrievalService.search(
        collection_name="kb1",
        query="q",
        search_mode="vector",
        filters={"file_name": "a.md", "header_path_prefix": "a.md >"},
        limit=5,
        vector_dimension=1024,
    )

    _args, kwargs = mock_retrieval.vector_search.call_args
    assert kwargs.get("metadata_filter") == {"file_name": "a.md"}


@patch("noesis.knowledge.retrieval.service.is_rerank_available", return_value=False)
@patch.object(knowledge_base, "_connected", True)
@patch.object(KbRetrievalService, "_get_retrieval")
def test_search_bm25_returns_nonzero_scores(
    mock_get_retrieval, _mock_rerank, mock_retrieval
):
    mock_retrieval.bm25_search_with_scores.return_value = [
        (
            Document(
                page_content="keyword hit",
                metadata={"file_name": "a.md", "point_id": "bm25-1"},
            ),
            2.5,
        ),
    ]
    mock_get_retrieval.return_value = mock_retrieval

    result = KbRetrievalService.search(
        collection_name="kb1",
        query="keyword",
        search_mode="bm25",
        limit=3,
        vector_dimension=1024,
    )

    hits = result.hits
    assert len(hits) == 1
    assert hits[0].search_mode == "bm25"
    assert hits[0].score == 2.5
    mock_retrieval.bm25_search_with_scores.assert_called_once()


@patch.object(knowledge_base, "_connected", True)
@patch.object(KbRetrievalService, "_get_retrieval")
def test_search_hybrid_uses_rrf(mock_get_retrieval, mock_retrieval):
    mock_retrieval.hybrid_search_with_scores.return_value = [
        (
            Document(
                page_content="fused",
                metadata={"file_name": "a.md", "point_id": "hy-1"},
            ),
            0.03278688524590164,
        ),
    ]
    mock_get_retrieval.return_value = mock_retrieval

    result = KbRetrievalService.search(
        collection_name="kb1",
        query="q",
        search_mode="hybrid",
        limit=5,
        recall_top_k=5,
        rrf_k=60,
        score_threshold=0,
        vector_dimension=1024,
    )

    hits = result.hits
    assert len(hits) == 1
    assert hits[0].search_mode == "hybrid"
    assert hits[0].score > 0
    mock_retrieval.hybrid_search_with_scores.assert_called_once_with(
        "q",
        k=5,
        rrf_k=60,
        metadata_filter=None,
    )


@patch.object(knowledge_base, "_client")
@patch.object(knowledge_base, "_connected", True)
def test_fetch_chunks_by_chunk_index_field(mock_client):
    point0 = MagicMock()
    point0.payload = {
        "chunk_index": 0,
        "page_content": "chunk-zero",
    }
    point1 = MagicMock()
    point1.payload = {
        "chunk_index": 2,
        "page_content": "chunk-two",
    }
    mock_client.scroll.return_value = ([point0, point1], None)

    chunks = KbRetrievalService.fetch_chunks_by_indexes("col", [0, 2, 99])
    assert chunks == ["chunk-zero", "chunk-two"]


@patch.object(knowledge_base, "_client")
@patch.object(knowledge_base, "_connected", True)
def test_fetch_full_document_by_file_name_sorted(mock_client):
    p0 = MagicMock()
    p0.payload = {
        "file_name": "req.md",
        "chunk_index": 1,
        "page_content": "第二部分",
    }
    p1 = MagicMock()
    p1.payload = {
        "file_name": "req.md",
        "chunk_index": 0,
        "page_content": "第一部分",
    }
    p2 = MagicMock()
    p2.payload = {
        "file_name": "other.md",
        "chunk_index": 0,
        "page_content": "忽略",
    }
    mock_client.scroll.return_value = ([p0, p1, p2], None)

    text = KbRetrievalService.fetch_full_document_by_file_name("col", "req.md")
    assert text == "第一部分\n\n第二部分"


def test_legacy_hit_is_explicitly_not_citable() -> None:
    hit = KbRetrievalService._doc_to_hit(
        Document(
            page_content="旧片段",
            metadata={"file_name": "legacy.md", "content_hash": "old"},
        ),
        0.5,
        "bm25",
        collection_name="kb1",
    )

    assert hit.identity_status == "legacy_unversioned"
    assert hit.citable is False
    assert hit.document_version_id is None


@patch("noesis.knowledge.retrieval.service.rerank_documents")
def test_rerank_preserves_evidence_identity(mock_rerank) -> None:
    mock_rerank.return_value = [(0, 0.97)]
    original = KbRetrievalService._doc_to_hit(
        Document(
            page_content="片段",
            metadata={
                "file_name": "doc.md",
                "document_id": "doc-1",
                "document_version_id": "docv-1",
                "segment_id": "seg-1",
                "locator": {"type": "page", "page_start": 1, "page_end": 1},
            },
        ),
        0.6,
        "hybrid",
        collection_name="kb1",
    )

    reranked = KbRetrievalService._apply_rerank("q", [original])

    assert reranked[0].document_id == "doc-1"
    assert reranked[0].document_version_id == "docv-1"
    assert reranked[0].segment_id == "seg-1"
    assert reranked[0].locator == original.locator
    assert reranked[0].citable is True


@patch("noesis.knowledge.retrieval.service.rerank_documents")
@patch("noesis.knowledge.retrieval.service.is_rerank_available", return_value=True)
@patch.object(knowledge_base, "_connected", True)
@patch.object(KbRetrievalService, "_get_retrieval")
def test_threshold_filters_low_rerank_scores(mock_get_retrieval, _avail, mock_rerank, mock_retrieval):
    """rerank 生效时，低于 score_threshold 的命中被滤除（默认 0.1，ERB 评测校准）。"""
    mock_get_retrieval.return_value = mock_retrieval
    mock_retrieval.vector_search.return_value = [
        (
            Document(page_content="relevant", metadata={"file_name": "a.md", "point_id": "pt-1"}),
            0.9,
        ),
        (
            Document(page_content="irrelevant", metadata={"file_name": "b.md", "point_id": "pt-2"}),
            0.8,
        ),
    ]
    # rerank 给出高/低两档分数：0.6 过阈值，0.05 被滤
    mock_rerank.return_value = [(0, 0.6), (1, 0.05)]

    result = KbRetrievalService.search(
        collection_name="kb1",
        query="agent",
        search_mode="vector",
        vector_dimension=1024,
    )
    assert [h.file_name for h in result.hits] == ["a.md"]


@patch("noesis.knowledge.retrieval.service.is_rerank_available", return_value=False)
@patch.object(knowledge_base, "_connected", True)
@patch.object(KbRetrievalService, "_get_retrieval")
def test_threshold_not_applied_without_rerank(mock_get_retrieval, _unavail, mock_retrieval):
    """rerank 未启用/降级时 score 是 RRF/向量分量纲，绝对阈值不应用（否则会滤空）。"""
    mock_get_retrieval.return_value = mock_retrieval
    # RRF 融合分量纲 ~0.0x，远低于阈值；不应被默认阈值滤掉
    mock_retrieval.vector_search.return_value = [
        (
            Document(page_content="fused", metadata={"file_name": "a.md", "point_id": "pt-1"}),
            0.03,
        ),
    ]

    result = KbRetrievalService.search(
        collection_name="kb1",
        query="q",
        search_mode="vector",
        vector_dimension=1024,
    )
    assert len(result.hits) == 1
