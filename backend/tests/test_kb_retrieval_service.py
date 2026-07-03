"""KbRetrievalService 单元测试（mock 检索底层）。"""
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document

from kb.retrieval import KbRetrievalService


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


@patch("kb.retrieval.service.is_qdrant_connected", return_value=True)
@patch.object(KbRetrievalService, "_get_retrieval")
def test_search_vector_mode(mock_get_retrieval, _mock_connected, mock_retrieval):
    mock_get_retrieval.return_value = mock_retrieval

    result = KbRetrievalService.search(
        collection_name="kb1",
        query="test",
        search_mode="vector",
        limit=3,
        vector_dimension=1024,
    )

    hits = result.hits
    assert len(hits) == 1
    assert hits[0].search_mode == "vector"
    assert hits[0].file_name == "a.md"
    assert hits[0].header_path == "a.md > Sec"
    assert result.timing.total_ms >= 0
    assert result.timing.recall_hits == 1
    assert result.timing.final_hits == 1
    mock_retrieval.vector_search.assert_called_once()


@patch("kb.retrieval.service.is_qdrant_connected", return_value=True)
@patch.object(KbRetrievalService, "_get_retrieval")
def test_search_with_filters_splits_prefix(mock_get_retrieval, _mock_connected, mock_retrieval):
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


@patch("kb.retrieval.service.is_rerank_available", return_value=False)
@patch("kb.retrieval.service.is_qdrant_connected", return_value=True)
@patch.object(KbRetrievalService, "_get_retrieval")
def test_search_bm25_returns_nonzero_scores(
    mock_get_retrieval, _mock_connected, _mock_rerank, mock_retrieval
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


@patch("kb.retrieval.service.is_qdrant_connected", return_value=True)
@patch.object(KbRetrievalService, "_get_retrieval")
def test_search_hybrid_uses_rrf(mock_get_retrieval, _mock_connected, mock_retrieval):
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


@patch("kb.retrieval.service.get_qdrant_client")
@patch("kb.retrieval.service.is_qdrant_connected", return_value=True)
def test_fetch_chunks_by_chunk_index_field(mock_connected, mock_client_fn):
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
    mock_client_fn.return_value.scroll.return_value = ([point0, point1], None)

    chunks = KbRetrievalService.fetch_chunks_by_indexes("col", [0, 2, 99])
    assert chunks == ["chunk-zero", "chunk-two"]


@patch("kb.retrieval.service.get_qdrant_client")
@patch("kb.retrieval.service.is_qdrant_connected", return_value=True)
def test_fetch_full_document_by_file_name_sorted(mock_connected, mock_client_fn):
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
    mock_client_fn.return_value.scroll.return_value = ([p0, p1, p2], None)

    text = KbRetrievalService.fetch_full_document_by_file_name("col", "req.md")
    assert text == "第一部分\n\n第二部分"
