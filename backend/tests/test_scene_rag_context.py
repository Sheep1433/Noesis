"""场景级多知识库 RAG 组装单测（mock KbRetrievalService）。"""

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from config.env import LangfuseConfig, QdrantConfig

from agent.case_generate.rag import (
    CHANNEL_CURRENT_REQUIREMENT,
    CHANNEL_HISTORICAL_REQUIREMENT,
    CHANNEL_HISTORICAL_TEST_CASES,
    _HybridRetriever,
    build_scene_rag_context,
)
from kb.retrieval import KbSearchHit


def _hit(hit_id: str, content: str, *, file_name: str = "a.md") -> KbSearchHit:
    return KbSearchHit(
        id=hit_id,
        score=0.9,
        content=content,
        file_name=file_name,
        search_mode="hybrid",
    )


SCENE = {
    "scene_name": "用户登录",
    "scene_description": "账号密码登录流程",
}


@pytest.mark.asyncio
async def test_build_scene_rag_context_three_channel_order_and_sections():
    """三路通道并行召回，Markdown 小节按 当前需求 → 历史需求 → 历史用例 顺序拼接。"""
    call_specs: list[tuple[str, dict | None]] = []

    async def _side_effect(self, query, *, limit=3, filters=None, vector_dimension=1024):
        call_specs.append((self.collection_name, filters))
        return [_hit(f"{self.collection_name}-1", f"content-{self.collection_name}")]

    qdrant_cfg = replace(QdrantConfig, case_rag_historical_requirements_enabled=True)
    with patch.object(_HybridRetriever, "search", new=_side_effect):
        with patch("agent.case_generate.rag.QdrantConfig", qdrant_cfg):
            context, trace = await build_scene_rag_context(
                SCENE,
                adopted_point_names=["密码错误提示"],
                source_file_names=["login.md"],
            )

    assert ("requirement_docs", {"file_name": "login.md"}) in call_specs
    assert ("requirement_docs", {"exclude_file_names": ["login.md"]}) in call_specs
    assert ("test_case_docs", None) in call_specs
    assert context.index("当前需求文档片段") < context.index("历史相关需求片段")
    assert context.index("历史相关需求片段") < context.index("历史测试用例参考")
    assert trace["scene_name"] == "用户登录"
    assert trace["channels"][CHANNEL_CURRENT_REQUIREMENT]["hit_ids"] == ["requirement_docs-1"]
    assert trace["channels"][CHANNEL_HISTORICAL_REQUIREMENT]["hit_ids"] == ["requirement_docs-1"]
    assert trace["channels"][CHANNEL_HISTORICAL_TEST_CASES]["hit_ids"] == ["test_case_docs-1"]


@pytest.mark.asyncio
async def test_build_scene_rag_context_skips_current_without_source_files():
    """无 source_file_names 时不召回当前需求通道。"""

    async def _side_effect(self, query, *, limit=3, filters=None, vector_dimension=1024):
        return [_hit(f"{self.collection_name}-1", "x")]

    with patch.object(_HybridRetriever, "search", new=_side_effect):
        context, trace = await build_scene_rag_context(SCENE)

    assert "当前需求文档片段" not in context
    assert CHANNEL_CURRENT_REQUIREMENT not in trace["channels"]
    assert CHANNEL_HISTORICAL_TEST_CASES in trace["channels"]


@pytest.mark.asyncio
async def test_build_scene_rag_context_historical_channel_disabled_by_default():
    """历史相关需求通道默认关闭。"""

    async def _side_effect(self, query, *, limit=3, filters=None, vector_dimension=1024):
        return []

    with patch.object(_HybridRetriever, "search", new=_side_effect):
        context, trace = await build_scene_rag_context(
            SCENE,
            source_file_names=["login.md"],
        )

    assert "历史相关需求片段" not in context
    assert CHANNEL_HISTORICAL_REQUIREMENT not in trace["channels"]


@pytest.mark.asyncio
async def test_build_scene_rag_context_langfuse_spans_when_enabled():
    """开关开启时为场景级与各 channel 创建 Langfuse retrieval span。"""
    span_updates: list[dict] = []

    class _FakeSpan:
        def update(self, **kwargs):
            span_updates.append(kwargs)

    class _FakeCtx:
        def __enter__(self):
            return _FakeSpan()

        def __exit__(self, *args):
            return False

    mock_client = MagicMock()
    mock_client.start_as_current_observation.return_value = _FakeCtx()
    observed_trace_contexts: list = []

    def _record_observation(**kwargs):
        observed_trace_contexts.append(kwargs.get("trace_context"))
        return _FakeCtx()

    mock_client.start_as_current_observation.side_effect = _record_observation

    async def _side_effect(self, query, *, limit=3, filters=None, vector_dimension=1024):
        return [_hit(f"{self.collection_name}-1", f"content-{self.collection_name}")]

    langfuse_cfg = replace(LangfuseConfig, langfuse_tracing_enabled=True)
    with patch("config.env.LangfuseConfig", langfuse_cfg):
        with patch("langfuse.get_client", return_value=mock_client):
            with patch.object(_HybridRetriever, "search", new=_side_effect):
                from domain.observability.langfuse import langfuse_workflow_context

                run_config = {
                    "metadata": {
                        "langfuse_session_id": "chat-abc",
                        "langfuse_trace_id": "chat-abc",
                    }
                }
                with langfuse_workflow_context(run_config):
                    context, trace = await build_scene_rag_context(
                        SCENE,
                        source_file_names=["login.md"],
                    )

    assert context
    assert trace["scene_name"] == "用户登录"
    assert mock_client.start_as_current_observation.call_count >= 3
    assert all(ctx == {"trace_id": "chat-abc"} for ctx in observed_trace_contexts)
