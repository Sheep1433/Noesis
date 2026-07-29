"""search_knowledge_base Tool 单元测试。"""
import json
import inspect
from unittest.mock import patch

from noesis.tools.kb_search_tool import (
    build_kb_search_tools,
    get_knowledge_document,
    list_knowledge_bases,
    resolve_search_collections,
    search_knowledge_bases_all,
)
import noesis.tools.kb_search_tool as kb_search_tool_module
from noesis.runtime.evidence import RetrievalManifest, bind_retrieval_manifest, reset_retrieval_manifest
from noesis_server.kb.retrieval import KbSearchHit, KbSearchResult, KbSearchTiming


@patch("noesis.tools.kb_search_tool.require_kb_collection_config_service")
@patch("noesis.tools.kb_search_tool.require_is_qdrant_connected", return_value=True)
@patch("noesis.tools.kb_search_tool.list_qdrant_collection_names", return_value=["req_docs", "kb_other"])
@patch("noesis.tools.kb_search_tool.require_qdrant_service")
@patch("noesis.tools.kb_search_tool.require_normalize_query_execution_params")
@patch("noesis.tools.kb_search_tool.require_kb_retrieval_service")
def test_search_all_collections_hybrid_and_merge(
    mock_retrieval, mock_normalize, mock_qdrant_cls, _names, _connected, _load_qp
):
    _load_qp.return_value.load_query_params_sync.return_value = {"search_mode": "hybrid"}
    mock_normalize.return_value.side_effect = lambda **kwargs: kwargs.get("request_overrides") or {}
    # normalize is require_normalize...() which returns callable
    mock_normalize.return_value = lambda **kwargs: {
        **(kwargs.get("request_overrides") or {}),
        "final_top_k": (kwargs.get("request_overrides") or {}).get("final_top_k", 10),
    }
    mock_qdrant_cls.get_collection.return_value = {
        "name": "x",
        "vector_dimension": 1024,
    }
    mock_search = mock_retrieval.return_value.search

    def _side_effect(*, collection_name: str, **kwargs):
        score = 0.9 if collection_name == "req_docs" else 0.5
        return KbSearchResult(
            hits=[
                KbSearchHit(
                    id="p1",
                    score=score,
                    content=f"片段-{collection_name}",
                    file_name="doc.md",
                    search_mode="hybrid",
                    document_id=f"doc-{collection_name}",
                    document_version_id=f"docv-{collection_name}",
                    segment_id=f"seg-{collection_name}",
                    locator={"type": "header", "path": ["登录"]},
                )
            ],
            timing=KbSearchTiming(
                prepare_ms=0.0,
                recall_ms=1.0,
                parse_ms=0.1,
                rerank_ms=0.0,
                post_ms=0.1,
                total_ms=1.2,
                rerank_applied=False,
                recall_hits=1,
                final_hits=1,
                search_mode="hybrid",
            ),
        )

    mock_search.side_effect = _side_effect

    token = bind_retrieval_manifest(RetrievalManifest(run_salt="test-run"))
    try:
        raw = search_knowledge_bases_all("如何登录", limit=5, tool_call_id="call-search")
    finally:
        reset_retrieval_manifest(token)
    data = json.loads(raw)
    assert len(data["results"]) == 2
    assert data["results"][0]["collection_name"] == "req_docs"
    assert data["results"][0]["citable"] is True
    assert data["results"][0]["excerpt"] == "片段-req_docs"
    assert data["results"][0]["evidence_id"].startswith("ev_")
    assert data["results"][0]["tool_call_ids"] == ["call-search"]
    assert "content" not in data["results"][0]
    assert mock_search.call_count == 2
    for call in mock_search.call_args_list:
        params = call.kwargs.get("query_execution_params") or {}
        assert params.get("final_top_k") == 5


@patch("noesis.tools.kb_search_tool.require_is_qdrant_connected", return_value=True)
@patch("noesis.tools.kb_search_tool.list_qdrant_collection_names", return_value=["req_docs", "kb_other"])
def test_resolve_scope_tool_param_over_default(_names, _connected):
    cols, err = resolve_search_collections(
        collection_names=["req_docs"],
        default_collection_names=["kb_other"],
    )
    assert err is None
    assert cols == ["req_docs"]


@patch("noesis.tools.kb_search_tool.require_is_qdrant_connected", return_value=True)
@patch("noesis.tools.kb_search_tool.list_qdrant_collection_names", return_value=["req_docs", "kb_other"])
def test_resolve_scope_session_default(_names, _connected):
    cols, err = resolve_search_collections(
        default_collection_names=["kb_other"],
    )
    assert err is None
    assert cols == ["kb_other"]


@patch("noesis.tools.kb_search_tool.require_is_qdrant_connected", return_value=True)
@patch("noesis.tools.kb_search_tool.list_qdrant_collection_names", return_value=["kb1", "kb2"])
def test_resolve_scope_rejects_collection_outside_user_selected_scope(_names, _connected):
    cols, err = resolve_search_collections(
        collection_names=["kb2"],
        default_collection_names=["kb1"],
        allowed_collection_names=["kb1"],
    )

    assert cols == []
    assert err is not None
    assert "kb2" in err


@patch("noesis.tools.kb_search_tool.require_is_qdrant_connected", return_value=True)
@patch("noesis.tools.kb_search_tool.list_qdrant_collection_names", return_value=["kb1", "kb2"])
def test_resolve_scope_rejects_mixed_request_when_scope_is_enforced(_names, _connected):
    cols, err = resolve_search_collections(
        collection_names=["kb1", "kb2"],
        allowed_collection_names=["kb1"],
    )

    assert cols == []
    assert err is not None
    assert "当前用户选定的检索范围" in err


@patch("noesis.tools.kb_search_tool.require_is_qdrant_connected", return_value=True)
@patch("noesis.tools.kb_search_tool.list_qdrant_collection_names", return_value=["kb1", "kb2"])
def test_resolve_scope_only_returns_user_selected_collections(_names, _connected):
    cols, err = resolve_search_collections(
        default_collection_names=["kb1"],
        allowed_collection_names=["kb1"],
    )

    assert err is None
    assert cols == ["kb1"]


@patch("noesis.tools.kb_search_tool.require_kb_collection_config_service")
@patch("noesis.tools.kb_search_tool.require_is_qdrant_connected", return_value=True)
@patch("noesis.tools.kb_search_tool.list_qdrant_collection_names", return_value=["req_docs", "kb_other"])
@patch("noesis.tools.kb_search_tool.require_qdrant_service")
@patch("noesis.tools.kb_search_tool.require_normalize_query_execution_params")
@patch("noesis.tools.kb_search_tool.require_kb_retrieval_service")
def test_search_scoped_collection_only(
    mock_retrieval, mock_normalize, mock_qdrant_cls, _names, _connected, _load_qp
):
    _load_qp.return_value.load_query_params_sync.return_value = {"search_mode": "hybrid"}
    mock_normalize.return_value = lambda **kwargs: {
        **(kwargs.get("request_overrides") or {}),
        "final_top_k": (kwargs.get("request_overrides") or {}).get("final_top_k", 10),
    }
    mock_qdrant_cls.get_collection.return_value = {
        "name": "x",
        "vector_dimension": 1024,
    }
    mock_search = mock_retrieval.return_value.search
    mock_search.return_value = KbSearchResult(
        hits=[
            KbSearchHit(
                id="p1",
                score=0.8,
                content="片段",
                file_name="doc.md",
                search_mode="hybrid",
            )
        ],
        timing=KbSearchTiming(
            prepare_ms=0.0,
            recall_ms=1.0,
            parse_ms=0.1,
            rerank_ms=0.0,
            post_ms=0.1,
            total_ms=1.2,
            rerank_applied=False,
            recall_hits=1,
            final_hits=1,
            search_mode="hybrid",
        ),
    )

    raw = search_knowledge_bases_all(
        "登录",
        limit=5,
        collection_names=["req_docs"],
    )
    data = json.loads(raw)
    assert len(data["results"]) == 1
    assert mock_search.call_count == 1
    assert mock_search.call_args.kwargs["collection_name"] == "req_docs"


@patch("noesis.tools.kb_search_tool.require_is_qdrant_connected", return_value=True)
@patch("noesis.tools.kb_search_tool.list_qdrant_collection_names", return_value=["kb1"])
def test_build_tools_when_collections_exist(_names, _connected):
    tools = build_kb_search_tools()
    assert len(tools) == 3
    names = {t.name for t in tools}
    assert names == {
        "list_knowledge_bases",
        "search_knowledge_base",
        "get_knowledge_document",
    }


def test_harness_kb_tool_does_not_import_platform_domain() -> None:
    source = inspect.getsource(kb_search_tool_module)
    assert "from noesis_server" not in source
    assert "import noesis_server" not in source


@patch("noesis.tools.kb_search_tool.require_is_qdrant_connected", return_value=False)
def test_build_empty_when_disconnected(_connected):
    assert build_kb_search_tools() == []


@patch("noesis.tools.kb_search_tool.require_is_qdrant_connected", return_value=True)
@patch("noesis.tools.kb_search_tool.list_qdrant_collection_names", return_value=[])
def test_search_returns_empty_when_no_collections(_names, _connected):
    raw = search_knowledge_bases_all("q")
    data = json.loads(raw)
    assert data["results"] == []


@patch("noesis.tools.kb_search_tool.require_is_qdrant_connected", return_value=True)
@patch("noesis.tools.kb_search_tool.list_qdrant_collection_names", return_value=["kb1"])
@patch("noesis.tools.kb_search_tool.require_qdrant_service")
def test_list_knowledge_bases(mock_qdrant_cls, _names, _connected):
    mock_qdrant_cls.get_collection.return_value = {
        "documents_count": 3,
        "points_count": 10,
    }
    data = json.loads(list_knowledge_bases())
    assert len(data["collections"]) == 1
    assert data["collections"][0]["collection_name"] == "kb1"


@patch("noesis.tools.kb_search_tool.require_is_qdrant_connected", return_value=True)
@patch("noesis.tools.kb_search_tool.list_qdrant_collection_names", return_value=["kb1"])
@patch("noesis.tools.kb_search_tool.require_kb_retrieval_service")
def test_get_knowledge_document(mock_retrieval, _names, _connected):
    mock_retrieval.return_value.fetch_full_document_by_file_name.return_value = "全文"
    data = json.loads(get_knowledge_document("kb1", "a.md"))
    assert data["content"] == "全文"
    assert data["truncated"] is False


@patch("noesis.tools.kb_search_tool.require_is_qdrant_connected", return_value=True)
@patch("noesis.tools.kb_search_tool.list_qdrant_collection_names", return_value=["kb1", "kb2"])
def test_get_knowledge_document_respects_scope(_names, _connected):
    data = json.loads(
        get_knowledge_document("kb2", "a.md", allowed_collection_names=["kb1"])
    )
    assert "不在当前会话检索范围内" in data["error"]
