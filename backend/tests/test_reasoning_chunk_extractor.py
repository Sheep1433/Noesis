"""langgraph_sse.reasoning 单元测试。"""
from types import SimpleNamespace

from domain.chat.streaming.reasoning import extract_reasoning_delta


def test_extract_from_additional_kwargs_reasoning_content() -> None:
    chunk = SimpleNamespace(
        content="",
        additional_kwargs={"reasoning_content": "思考片段"},
    )
    assert extract_reasoning_delta(chunk) == "思考片段"


def test_extract_from_additional_kwargs_reasoning_alias() -> None:
    chunk = SimpleNamespace(
        content="",
        additional_kwargs={"reasoning": "via openrouter"},
    )
    assert extract_reasoning_delta(chunk) == "via openrouter"


def test_extract_empty_returns_none() -> None:
    chunk = SimpleNamespace(content="hello", additional_kwargs={})
    assert extract_reasoning_delta(chunk) is None
