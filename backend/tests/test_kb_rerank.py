"""rerank 模块单测。"""
from unittest.mock import MagicMock, patch

import pytest

from kb.rerank.client import is_rerank_available, rerank_documents


@patch("kb.rerank.client.ModelConfig")
def test_is_rerank_available_requires_key_and_name(mock_cfg):
    mock_cfg.rerank_model_name = "gte-rerank-v2"
    mock_cfg.rerank_model_api_key = ""
    assert is_rerank_available() is False
    mock_cfg.rerank_model_api_key = "sk-test"
    assert is_rerank_available() is True


@patch("kb.rerank.client.httpx.Client")
@patch("kb.rerank.client.is_rerank_available", return_value=True)
@patch("kb.rerank.client.ModelConfig")
def test_rerank_documents_changes_order(mock_cfg, _avail, mock_client_cls):
    mock_cfg.rerank_model_name = "gte-rerank-v2"
    mock_cfg.rerank_model_api_key = "sk-test"

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "output": {
            "results": [
                {"index": 1, "relevance_score": 0.95},
                {"index": 0, "relevance_score": 0.2},
            ]
        }
    }
    mock_resp.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_resp
    mock_client_cls.return_value = mock_client

    ranked = rerank_documents("query", ["doc-a", "doc-b"], top_n=2)
    assert ranked[0] == (1, 0.95)
    assert ranked[1] == (0, 0.2)


@patch("kb.rerank.client.rerank_documents", side_effect=RuntimeError("api down"))
@patch("kb.rerank.client.is_rerank_available", return_value=True)
@patch("kb.retrieval.service.is_rerank_available", return_value=True)
@patch("kb.retrieval.service.is_qdrant_connected", return_value=True)
@patch.object(
    __import__("kb.retrieval.service", fromlist=["KbRetrievalService"]).KbRetrievalService,
    "_get_retrieval",
)
def test_retrieval_degrades_when_rerank_fails(mock_get_retrieval, _conn, _svc_avail, _client_avail, _rerank):
    from langchain_core.documents import Document

    from kb.retrieval import KbRetrievalService

    retrieval = MagicMock()
    retrieval.hybrid_search_with_scores.return_value = [
        (Document(page_content="a", metadata={"file_name": "a.md", "point_id": "1"}), 0.5),
        (Document(page_content="b", metadata={"file_name": "b.md", "point_id": "2"}), 0.9),
    ]
    mock_get_retrieval.return_value = retrieval

    result = KbRetrievalService.search(
        collection_name="kb1",
        query="test",
        query_execution_params={"use_reranker": True, "final_top_k": 2, "recall_top_k": 10},
        vector_dimension=1024,
    )
    hits = result.hits
    assert len(hits) == 2
    assert hits[0].score >= hits[1].score or hits[0].file_name == "b.md"


@patch("kb.retrieval.service.rerank_documents")
@patch("kb.retrieval.service.is_rerank_available", return_value=True)
@patch("kb.retrieval.service.is_qdrant_connected", return_value=True)
@patch.object(
    __import__("kb.retrieval.service", fromlist=["KbRetrievalService"]).KbRetrievalService,
    "_get_retrieval",
)
def test_retrieval_caps_rerank_input(mock_get_retrieval, _conn, _avail, mock_rerank):
    """rerank 只吃 rerank_top_k 条，控制 API documents 计费。"""
    from langchain_core.documents import Document

    from kb.retrieval import KbRetrievalService

    docs = [
        (Document(page_content=f"d{i}", metadata={"file_name": f"{i}.md", "point_id": str(i)}), float(i))
        for i in range(10, 0, -1)
    ]
    retrieval = MagicMock()
    retrieval.hybrid_search_with_scores.return_value = docs
    mock_get_retrieval.return_value = retrieval
    mock_rerank.return_value = [(0, 0.9), (1, 0.8), (2, 0.1)]

    result = KbRetrievalService.search(
        collection_name="kb1",
        query="test",
        query_execution_params={
            "use_reranker": True,
            "search_mode": "hybrid",
            "final_top_k": 3,
            "recall_top_k": 10,
            "rerank_top_k": 3,
        },
        vector_dimension=1024,
    )

    mock_rerank.assert_called_once()
    _args, _kwargs = mock_rerank.call_args
    assert _args[0] == "test"
    assert len(_args[1]) == 3
    assert len(result.hits) == 3
