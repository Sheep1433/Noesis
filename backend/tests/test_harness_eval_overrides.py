from noesis.config.checkpointer import get_checkpointer, temporary_checkpointer
from noesis.llm import build_chat_model
from noesis.runtime.deps import (
    require_is_qdrant_connected,
    require_kb_collection_config_service,
    require_kb_retrieval_service,
    require_normalize_query_execution_params,
    require_qdrant_service,
    temporary_kb_runtime,
)


def test_temporary_checkpointer_is_nested_and_restored():
    outer = object()
    inner = object()
    with temporary_checkpointer(outer):
        assert get_checkpointer() is outer
        with temporary_checkpointer(inner):
            assert get_checkpointer() is inner
        assert get_checkpointer() is outer


def test_temporary_kb_runtime_restores_outer_binding():
    outer_config = object()
    outer_retrieval = object()
    inner_config = object()
    inner_retrieval = object()
    outer_qdrant = object()
    inner_qdrant = object()

    with temporary_kb_runtime(
        collection_config_service=outer_config,
        qdrant_service_factory=lambda: outer_qdrant,
        is_qdrant_connected=lambda: True,
        normalize_query_execution_params=lambda **kwargs: kwargs,
        retrieval_service=outer_retrieval,
    ):
        with temporary_kb_runtime(
            collection_config_service=inner_config,
            qdrant_service_factory=lambda: inner_qdrant,
            is_qdrant_connected=lambda: False,
            normalize_query_execution_params=lambda **kwargs: {"inner": kwargs},
            retrieval_service=inner_retrieval,
        ):
            assert require_kb_collection_config_service() is inner_config
            assert require_qdrant_service() is inner_qdrant
            assert require_is_qdrant_connected() is False
            assert require_kb_retrieval_service() is inner_retrieval
            assert require_normalize_query_execution_params()(x=1) == {"inner": {"x": 1}}

        assert require_kb_collection_config_service() is outer_config
        assert require_qdrant_service() is outer_qdrant
        assert require_is_qdrant_connected() is True
        assert require_kb_retrieval_service() is outer_retrieval


def test_build_chat_model_is_public():
    assert callable(build_chat_model)
