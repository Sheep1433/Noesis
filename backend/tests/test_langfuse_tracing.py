"""Langfuse RunnableConfig 合并单元测试（不依赖真实 Langfuse 服务）。"""

from unittest.mock import MagicMock, patch

from utils import langfuse_tracing


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
    assert patch_result["metadata"]["langfuse_trace_id"] == "workflow-trace-1"
    mock_handler_cls.assert_called_once_with(
        trace_context={"trace_id": "workflow-trace-1"},
    )


def test_normalize_langfuse_trace_id_strips_uuid_hyphens():
    uuid_raw = "d5f2c3f4-729c-4779-8dbe-307467f276e3"
    assert langfuse_tracing.normalize_langfuse_trace_id(uuid_raw) == (
        "d5f2c3f4729c47798dbe307467f276e3"
    )
    assert langfuse_tracing.normalize_langfuse_trace_id("workflow-trace-1") == (
        "workflow-trace-1"
    )


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
    with patch("config.env.LangfuseConfig.langfuse_tracing_enabled", False):
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
    with patch("config.env.LangfuseConfig.langfuse_tracing_enabled", True):
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
    with patch("config.env.LangfuseConfig.langfuse_tracing_enabled", True):
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


def test_hits_to_langfuse_payload():
    hit = MagicMock(id="h1", score=0.9, file_name="a.md", content="正文")
    payload = langfuse_tracing.hits_to_langfuse_payload([hit])
    assert payload == [
        {"id": "h1", "score": 0.9, "file_name": "a.md", "content": "正文"}
    ]
