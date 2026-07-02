"""search_knowledge_base Tool 单元测试。"""
import json
from unittest.mock import patch

from agent.tools.kb_search_tool import (
    build_kb_search_tools,
    get_knowledge_document,
    list_knowledge_bases,
    resolve_search_collections,
    search_knowledge_bases_all,
)
from kb.retrieval import KbSearchHit


@patch("agent.tools.kb_search_tool.KbCollectionConfigService.load_query_params_sync", return_value={"search_mode": "hybrid"})
@patch("agent.tools.kb_search_tool.is_qdrant_connected", return_value=True)
@patch("agent.tools.kb_search_tool.list_qdrant_collection_names", return_value=["req_docs", "kb_other"])
@patch("agent.tools.kb_search_tool.QdrantService")
@patch("agent.tools.kb_search_tool.KbRetrievalService.search")
def test_search_all_collections_hybrid_and_merge(
    mock_search, mock_qdrant_cls, _names, _connected, _load_qp
):
    mock_qdrant_cls.return_value.get_collection.return_value = {
        "name": "x",
        "vector_dimension": 1024,
    }

    def _side_effect(*, collection_name: str, **kwargs):
        score = 0.9 if collection_name == "req_docs" else 0.5
        return [
            KbSearchHit(
                id="p1",
                score=score,
                content=f"片段-{collection_name}",
                file_name="doc.md",
                search_mode="hybrid",
            )
        ]

    mock_search.side_effect = _side_effect

    raw = search_knowledge_bases_all("如何登录", limit=5)
    data = json.loads(raw)
    assert len(data["hits"]) == 2
    assert data["hits"][0]["collection_name"] == "req_docs"
    assert mock_search.call_count == 2
    for call in mock_search.call_args_list:
        params = call.kwargs.get("query_execution_params") or {}
        assert params.get("final_top_k") == 5


@patch("agent.tools.kb_search_tool.is_qdrant_connected", return_value=True)
@patch("agent.tools.kb_search_tool.list_qdrant_collection_names", return_value=["req_docs", "kb_other"])
def test_resolve_scope_tool_param_over_default(_names, _connected):
    cols, err = resolve_search_collections(
        collection_names=["req_docs"],
        default_collection_names=["kb_other"],
    )
    assert err is None
    assert cols == ["req_docs"]


@patch("agent.tools.kb_search_tool.is_qdrant_connected", return_value=True)
@patch("agent.tools.kb_search_tool.list_qdrant_collection_names", return_value=["req_docs", "kb_other"])
def test_resolve_scope_session_default(_names, _connected):
    cols, err = resolve_search_collections(
        default_collection_names=["kb_other"],
    )
    assert err is None
    assert cols == ["kb_other"]


@patch("agent.tools.kb_search_tool.KbCollectionConfigService.load_query_params_sync", return_value={"search_mode": "hybrid"})
@patch("agent.tools.kb_search_tool.is_qdrant_connected", return_value=True)
@patch("agent.tools.kb_search_tool.list_qdrant_collection_names", return_value=["req_docs", "kb_other"])
@patch("agent.tools.kb_search_tool.QdrantService")
@patch("agent.tools.kb_search_tool.KbRetrievalService.search")
def test_search_scoped_collection_only(
    mock_search, mock_qdrant_cls, _names, _connected, _load_qp
):
    mock_qdrant_cls.return_value.get_collection.return_value = {
        "name": "x",
        "vector_dimension": 1024,
    }
    mock_search.return_value = [
        KbSearchHit(
            id="p1",
            score=0.8,
            content="片段",
            file_name="doc.md",
            search_mode="hybrid",
        )
    ]

    raw = search_knowledge_bases_all(
        "登录",
        limit=5,
        collection_names=["req_docs"],
    )
    data = json.loads(raw)
    assert len(data["hits"]) == 1
    assert mock_search.call_count == 1
    assert mock_search.call_args.kwargs["collection_name"] == "req_docs"


@patch("agent.tools.kb_search_tool.is_qdrant_connected", return_value=True)
@patch("agent.tools.kb_search_tool.list_qdrant_collection_names", return_value=["kb1"])
def test_build_tools_when_collections_exist(_names, _connected):
    tools = build_kb_search_tools()
    assert len(tools) == 3
    names = {t.name for t in tools}
    assert names == {
        "list_knowledge_bases",
        "search_knowledge_base",
        "get_knowledge_document",
    }


@patch("agent.tools.kb_search_tool.is_qdrant_connected", return_value=False)
def test_build_empty_when_disconnected(_connected):
    assert build_kb_search_tools() == []


@patch("agent.tools.kb_search_tool.is_qdrant_connected", return_value=True)
@patch("agent.tools.kb_search_tool.list_qdrant_collection_names", return_value=[])
def test_search_returns_empty_when_no_collections(_names, _connected):
    raw = search_knowledge_bases_all("q")
    data = json.loads(raw)
    assert data["hits"] == []


@patch("agent.tools.kb_search_tool.is_qdrant_connected", return_value=True)
@patch("agent.tools.kb_search_tool.list_qdrant_collection_names", return_value=["kb1"])
@patch("agent.tools.kb_search_tool.QdrantService")
def test_list_knowledge_bases(mock_qdrant_cls, _names, _connected):
    mock_qdrant_cls.return_value.get_collection.return_value = {
        "documents_count": 3,
        "points_count": 10,
    }
    data = json.loads(list_knowledge_bases())
    assert len(data["collections"]) == 1
    assert data["collections"][0]["collection_name"] == "kb1"


@patch("agent.tools.kb_search_tool.is_qdrant_connected", return_value=True)
@patch("agent.tools.kb_search_tool.list_qdrant_collection_names", return_value=["kb1"])
@patch("agent.tools.kb_search_tool.KbRetrievalService.fetch_full_document_by_file_name", return_value="全文")
def test_get_knowledge_document(_fetch, _names, _connected):
    data = json.loads(get_knowledge_document("kb1", "a.md"))
    assert data["content"] == "全文"
    assert data["truncated"] is False


@patch("agent.tools.kb_search_tool.is_qdrant_connected", return_value=True)
@patch("agent.tools.kb_search_tool.list_qdrant_collection_names", return_value=["kb1", "kb2"])
def test_get_knowledge_document_respects_scope(_names, _connected):
    data = json.loads(
        get_knowledge_document("kb2", "a.md", allowed_collection_names=["kb1"])
    )
    assert "不在当前会话检索范围内" in data["error"]
