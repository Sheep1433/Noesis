"""Langfuse RunnableConfig 合并单元测试（不依赖真实 Langfuse 服务）。"""

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from noesis.config.env import LangfuseConfig
from server import langfuse as langfuse_tracing


def _langfuse_config(*, tracing_enabled: bool) -> LangfuseConfig:
    return replace(LangfuseConfig, langfuse_tracing_enabled=tracing_enabled)


def test_merge_langfuse_disabled_no_callbacks():
    base = {"configurable": {"thread_id": "t1"}, "recursion_limit": 10}
    out = langfuse_tracing.merge_langfuse_runnable_config(
        base,
        langfuse_session_id="sess-1",
        qa_type="COMMON_QA",
        enabled=False,
    )
    assert out == base


def test_merge_langfuse_enabled_session_patch():
    base = {"configurable": {"thread_id": "t1"}, "recursion_limit": 10}
    mock_handler = MagicMock(name="CallbackHandler")
    with patch.object(
        langfuse_tracing,
        "_langfuse_config_patch",
        return_value={
            "callbacks": [mock_handler],
            "metadata": {
                "langfuse_session_id": "sess-1",
                "langfuse_trace_id": "sess-1",
                "qa_type": "X",
            },
        },
    ):
        out = langfuse_tracing.merge_langfuse_runnable_config(
            base,
            langfuse_session_id="sess-1",
            qa_type="X",
            enabled=True,
            langfuse_trace_id="sess-1",
        )
    assert out["callbacks"] == [mock_handler]
    assert out["metadata"]["langfuse_session_id"] == "sess-1"
    assert out["metadata"]["langfuse_trace_id"] == "sess-1"
    assert out["metadata"]["qa_type"] == "X"
    assert out["configurable"] == {"thread_id": "t1"}


def test_merge_langfuse_trace_id_passes_to_callback_handler():
    mock_handler_cls = MagicMock()
    mock_handler_cls.return_value = MagicMock(last_trace_id="tid-1")
    with patch("langfuse.langchain.CallbackHandler", mock_handler_cls):
        patch_result = langfuse_tracing._langfuse_config_patch(
            langfuse_session_id="sess-1",
            qa_type="TEST_CASE_QA",
            enabled=True,
            langfuse_trace_id="workflow-trace-1",
        )
    normalized = langfuse_tracing.normalize_langfuse_trace_id("workflow-trace-1")
    assert patch_result["metadata"]["langfuse_trace_id"] == normalized
    mock_handler_cls.assert_called_once_with(
        trace_context={"trace_id": normalized},
    )


def test_normalize_langfuse_trace_id_strips_uuid_hyphens():
    uuid_raw = "d5f2c3f4-729c-4779-8dbe-307467f276e3"
    assert langfuse_tracing.normalize_langfuse_trace_id(uuid_raw) == (
        "d5f2c3f4729c47798dbe307467f276e3"
    )
    # 非法 id 确定性哈希为合法 32 hex（原样透传会让 span 上报抛异常，链路静默失效）
    hashed = langfuse_tracing.normalize_langfuse_trace_id("workflow-trace-1")
    assert len(hashed) == 32 and all(c in "0123456789abcdef" for c in hashed)
    assert hashed == langfuse_tracing.normalize_langfuse_trace_id("workflow-trace-1")


def test_langfuse_config_patch_normalizes_session_uuid_trace_id():
    mock_handler_cls = MagicMock()
    mock_handler_cls.return_value = MagicMock()
    session_uuid = "d5f2c3f4-729c-4779-8dbe-307467f276e3"
    with patch("langfuse.langchain.CallbackHandler", mock_handler_cls):
        patch_result = langfuse_tracing._langfuse_config_patch(
            langfuse_session_id=session_uuid,
            qa_type="TEST_CASE_QA",
            enabled=True,
            langfuse_trace_id=session_uuid,
        )
    expected = "d5f2c3f4729c47798dbe307467f276e3"
    assert patch_result["metadata"]["langfuse_trace_id"] == expected
    mock_handler_cls.assert_called_once_with(trace_context={"trace_id": expected})


def test_langfuse_trace_context_from_config():
    assert langfuse_tracing.langfuse_trace_context_from_config(
        {"metadata": {"langfuse_trace_id": "t-99"}}
    ) == {"trace_id": "t-99"}
    assert langfuse_tracing.langfuse_trace_context_from_config(None) is None


def test_capture_langfuse_trace_id_from_metadata():
    cfg = {"metadata": {"langfuse_trace_id": "t-meta"}}
    assert langfuse_tracing.capture_langfuse_trace_id(cfg) == "t-meta"


def test_langfuse_session_id_from_config():
    assert langfuse_tracing.langfuse_session_id_from_config(
        {"metadata": {"langfuse_session_id": "chat-1"}}
    ) == "chat-1"
    assert langfuse_tracing.langfuse_session_id_from_config(None) is None


def test_langfuse_workflow_context_disabled_is_noop():
    run_config = {"metadata": {"langfuse_session_id": "chat-1"}}
    with patch("noesis.config.env.LangfuseConfig", _langfuse_config(tracing_enabled=False)):
        with langfuse_tracing.langfuse_workflow_context(run_config):
            assert langfuse_tracing._lf_session_id.get() is None


def test_langfuse_workflow_context_propagates_from_run_config():
    run_config = {
        "metadata": {
            "langfuse_session_id": "chat-1",
            "langfuse_trace_id": "chat-1",
            "qa_type": "TEST_CASE_QA",
        }
    }
    mock_propagate = MagicMock()
    mock_propagate.__enter__ = MagicMock(return_value=None)
    mock_propagate.__exit__ = MagicMock(return_value=False)
    with patch("noesis.config.env.LangfuseConfig", _langfuse_config(tracing_enabled=True)):
        with patch("langfuse.propagate_attributes", return_value=mock_propagate) as pa:
            with langfuse_tracing.langfuse_workflow_context(run_config):
                assert langfuse_tracing._lf_session_id.get() == "chat-1"
                assert langfuse_tracing._lf_trace_context.get() == {"trace_id": "chat-1"}
    pa.assert_called_once_with(
        session_id="chat-1",
        metadata={"qa_type": "TEST_CASE_QA"},
    )


def test_langfuse_retrieval_observation_disabled_is_noop():
    with langfuse_tracing.langfuse_retrieval_observation(
        name="rag/test",
        input_data={"query": "q"},
        enabled=False,
    ) as span:
        assert span is None


def test_langfuse_retrieval_observation_reads_workflow_context():
    mock_span = MagicMock()
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_span)
    mock_cm.__exit__ = MagicMock(return_value=False)
    mock_client = MagicMock()
    mock_client.start_as_current_observation.return_value = mock_cm
    run_config = {
        "metadata": {
            "langfuse_session_id": "chat-1",
            "langfuse_trace_id": "trace-1",
        }
    }
    with patch("noesis.config.env.LangfuseConfig", _langfuse_config(tracing_enabled=True)):
        with patch("langfuse.get_client", return_value=mock_client):
            with langfuse_tracing.langfuse_workflow_context(run_config):
                with langfuse_tracing.langfuse_retrieval_observation(
                    name="rag/requirement_docs",
                    input_data={"query": "登录"},
                    enabled=True,
                ) as span:
                    assert span is mock_span
    mock_client.start_as_current_observation.assert_called_once_with(
        name="rag/requirement_docs",
        as_type="retrieval",
        input={"query": "登录"},
        trace_context={"trace_id": "trace-1"},
    )
    mock_span.update_trace.assert_called_once_with(session_id="chat-1")


# --------------------------------------------- 异常传播（yield 不得落在 except 内）
# with 块内的业务异常必须原样传播：吞掉会替换成 contextlib 的
# "generator didn't stop after throw()"，线上错误分类与评测报错全部失真


def _observation_cm(span=None):
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=span)
    mock_cm.__exit__ = MagicMock(return_value=False)
    return mock_cm


def test_eval_langfuse_observation_propagates_block_exception():
    mock_client = MagicMock()
    mock_client.start_as_current_observation.return_value = _observation_cm(MagicMock())
    with patch("noesis.config.env.LangfuseConfig", _langfuse_config(tracing_enabled=True)):
        with patch("langfuse.get_client", return_value=mock_client):
            with pytest.raises(ValueError, match="业务异常"):
                with langfuse_tracing.eval_langfuse_observation(name="arm", input_data={}):
                    raise ValueError("业务异常")


def test_eval_langfuse_observation_degrades_when_client_fails():
    with patch("noesis.config.env.LangfuseConfig", _langfuse_config(tracing_enabled=True)):
        with patch("langfuse.get_client", side_effect=RuntimeError("langfuse down")):
            with langfuse_tracing.eval_langfuse_observation(name="arm") as span:
                assert span is None


def test_langfuse_workflow_context_propagates_block_exception():
    run_config = {"metadata": {"langfuse_session_id": "chat-1"}}
    mock_propagate = MagicMock()
    mock_propagate.__enter__ = MagicMock(return_value=None)
    mock_propagate.__exit__ = MagicMock(return_value=False)
    with patch("noesis.config.env.LangfuseConfig", _langfuse_config(tracing_enabled=True)):
        with patch("langfuse.propagate_attributes", return_value=mock_propagate):
            with pytest.raises(ValueError, match="业务异常"):
                with langfuse_tracing.langfuse_workflow_context(run_config):
                    raise ValueError("业务异常")
    assert langfuse_tracing._lf_session_id.get() is None


def test_langfuse_workflow_context_degrades_on_langfuse_failure():
    run_config = {"metadata": {"langfuse_session_id": "chat-1"}}
    with patch("noesis.config.env.LangfuseConfig", _langfuse_config(tracing_enabled=True)):
        with patch("langfuse.propagate_attributes", side_effect=RuntimeError("langfuse down")):
            with langfuse_tracing.langfuse_workflow_context(run_config):
                # 降级仅跳过 propagate，session/trace 上下文仍注入供下游读取
                assert langfuse_tracing._lf_session_id.get() == "chat-1"
    assert langfuse_tracing._lf_session_id.get() is None


def test_langfuse_retrieval_observation_propagates_block_exception():
    mock_client = MagicMock()
    mock_client.start_as_current_observation.return_value = _observation_cm(MagicMock())
    with patch("noesis.config.env.LangfuseConfig", _langfuse_config(tracing_enabled=True)):
        with patch("langfuse.get_client", return_value=mock_client):
            with pytest.raises(ValueError, match="业务异常"):
                with langfuse_tracing.langfuse_retrieval_observation(name="rag/x", enabled=True):
                    raise ValueError("业务异常")


# ------------------------------------------------- record_eval_* 的 trace 嵌套
# SDK 收到带 trace_id 的 trace_context 会走 remote-parent 分支挂 trace 根层；
# 活动 OTel span 内必须省略 trace_context，generation/tool 才会挂到臂 span 之下


def _eval_active():
    return langfuse_tracing._EvalLangfuseActive(
        line="compression", tag="t1", session_id="s1", trace_id="a" * 32)


def test_record_eval_generation_nests_under_active_span():
    mock_client = MagicMock()
    mock_client.start_as_current_observation.return_value = _observation_cm(MagicMock())
    active_span = MagicMock()
    active_span.get_span_context.return_value.is_valid = True
    tok_active = langfuse_tracing._eval_langfuse_active.set(_eval_active())
    tok_trace = langfuse_tracing._lf_trace_context.set({"trace_id": "a" * 32})
    try:
        with patch("langfuse.get_client", return_value=mock_client):
            with patch("opentelemetry.trace.get_current_span", return_value=active_span):
                langfuse_tracing.record_eval_generation(
                    name="llm/m", input_messages=[], output_payload="o")
    finally:
        langfuse_tracing._eval_langfuse_active.reset(tok_active)
        langfuse_tracing._lf_trace_context.reset(tok_trace)
    kwargs = mock_client.start_as_current_observation.call_args.kwargs
    assert kwargs["trace_context"] is None


def test_record_eval_generation_falls_back_to_trace_context_without_span():
    mock_client = MagicMock()
    mock_client.start_as_current_observation.return_value = _observation_cm(MagicMock())
    no_span = MagicMock()
    no_span.get_span_context.return_value.is_valid = False
    tok_active = langfuse_tracing._eval_langfuse_active.set(_eval_active())
    tok_trace = langfuse_tracing._lf_trace_context.set({"trace_id": "a" * 32})
    try:
        with patch("langfuse.get_client", return_value=mock_client):
            with patch("opentelemetry.trace.get_current_span", return_value=no_span):
                langfuse_tracing.record_eval_generation(
                    name="llm/m", input_messages=[], output_payload="o")
    finally:
        langfuse_tracing._eval_langfuse_active.reset(tok_active)
        langfuse_tracing._lf_trace_context.reset(tok_trace)
    kwargs = mock_client.start_as_current_observation.call_args.kwargs
    assert kwargs["trace_context"] == {"trace_id": "a" * 32}


def test_record_eval_tool_span_nests_under_active_span():
    mock_client = MagicMock()
    mock_client.start_as_current_observation.return_value = _observation_cm(MagicMock())
    active_span = MagicMock()
    active_span.get_span_context.return_value.is_valid = True
    tok_active = langfuse_tracing._eval_langfuse_active.set(_eval_active())
    tok_trace = langfuse_tracing._lf_trace_context.set({"trace_id": "a" * 32})
    try:
        with patch("langfuse.get_client", return_value=mock_client):
            with patch("opentelemetry.trace.get_current_span", return_value=active_span):
                langfuse_tracing.record_eval_tool_span(
                    name="search_history", input_data={"query": "q"}, output_text="r")
    finally:
        langfuse_tracing._eval_langfuse_active.reset(tok_active)
        langfuse_tracing._lf_trace_context.reset(tok_trace)
    kwargs = mock_client.start_as_current_observation.call_args.kwargs
    assert kwargs["trace_context"] is None


def test_hits_to_langfuse_payload():
    hit = MagicMock(id="h1", score=0.9, file_name="a.md", content="正文")
    payload = langfuse_tracing.hits_to_langfuse_payload([hit])
    assert payload == [
        {"id": "h1", "score": 0.9, "file_name": "a.md", "content": "正文"}
    ]


def test_otel_exporter_uses_direct_http_session() -> None:
    import requests
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )

    langfuse_tracing._otel_exporter_direct_http_patched = False
    langfuse_tracing._patch_langfuse_otel_direct_http()
    exporter = OTLPSpanExporter(endpoint="http://127.0.0.1:3000/api/public/otel/v1/traces")
    assert isinstance(exporter._session, requests.Session)
    assert exporter._session.trust_env is False
