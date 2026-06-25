"""langgraph_sse.reasoning 单元测试。"""
from types import SimpleNamespace

from domain.chat.streaming.reasoning import (
    extract_reasoning_delta,
    extract_text_content,
    unsent_text_suffix,
)


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


def test_extract_text_content_from_string() -> None:
    msg = SimpleNamespace(content="你好")
    assert extract_text_content(msg) == "你好"


def test_extract_text_content_from_blocks() -> None:
    msg = SimpleNamespace(content=[{"type": "text", "text": "你好"}, {"type": "text", "text": "！"}])
    assert extract_text_content(msg) == "你好！"


def test_unsent_text_suffix() -> None:
    assert unsent_text_suffix("你好", "") == "你好"
    assert unsent_text_suffix("你好世界", "你好") == "世界"
    assert unsent_text_suffix("你好", "你好") == ""
    assert unsent_text_suffix("", "x") == ""
