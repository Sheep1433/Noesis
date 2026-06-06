"""search_knowledge_base Tool 单元测试。"""
import json
from unittest.mock import MagicMock, patch

from agent.tools.kb_search_tool import (
    build_kb_search_tools,
    search_knowledge_bases_all,
)
from kb.retrieval import KbSearchHit


@patch("agent.tools.kb_search_tool.is_qdrant_connected", return_value=True)
@patch("agent.tools.kb_search_tool.list_qdrant_collection_names", return_value=["req_docs", "kb_other"])
@patch("agent.tools.kb_search_tool.QdrantService")
@patch("agent.tools.kb_search_tool.KbRetrievalService.search")
def test_search_all_collections_hybrid_and_merge(
    mock_search, mock_qdrant_cls, _names, _connected
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
        assert call.kwargs["search_mode"] == "hybrid"


@patch("agent.tools.kb_search_tool.is_qdrant_connected", return_value=True)
@patch("agent.tools.kb_search_tool.list_qdrant_collection_names", return_value=["kb1"])
def test_build_tool_when_collections_exist(_names, _connected):
    tools = build_kb_search_tools()
    assert len(tools) == 1
    assert tools[0].name == "search_knowledge_base"


@patch("agent.tools.kb_search_tool.is_qdrant_connected", return_value=False)
def test_build_empty_when_disconnected(_connected):
    assert build_kb_search_tools() == []


@patch("agent.tools.kb_search_tool.is_qdrant_connected", return_value=True)
@patch("agent.tools.kb_search_tool.list_qdrant_collection_names", return_value=[])
def test_search_returns_empty_when_no_collections(_names, _connected):
    raw = search_knowledge_bases_all("q")
    data = json.loads(raw)
    assert data["hits"] == []
