from noesis.config.checkpointer import get_checkpointer, temporary_checkpointer
from noesis.llm import build_chat_model


def test_temporary_checkpointer_is_nested_and_restored():
    outer = object()
    inner = object()
    with temporary_checkpointer(outer):
        assert get_checkpointer() is outer
        with temporary_checkpointer(inner):
            assert get_checkpointer() is inner
        assert get_checkpointer() is outer


def test_build_chat_model_is_public():
    assert callable(build_chat_model)
