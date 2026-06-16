from unittest.mock import MagicMock, patch

import pytest
from langchain_openai import ChatOpenAI

from llm.factory import _OPENCODE_DEFAULT_BASE_URL, _OPENCODE_DEFAULT_HEADERS, _build_chat_model


def test_build_chat_model_opencode_uses_required_headers() -> None:
    with patch("llm.factory.ChatOpenAI", return_value=MagicMock()) as chat_openai:
        _build_chat_model(
            model_type="opencode",
            model_name="deepseek-v4-flash-free",
            temperature=0.75,
            model_base_url="https://opencode.ai/zen/v1",
            model_api_key="public",
        )

    chat_openai.assert_called_once()
    kwargs = chat_openai.call_args.kwargs
    assert kwargs["model"] == "deepseek-v4-flash-free"
    assert kwargs["base_url"] == "https://opencode.ai/zen/v1"
    assert kwargs["api_key"] == "public"
    assert kwargs["default_headers"] == _OPENCODE_DEFAULT_HEADERS


def test_build_chat_model_opencode_falls_back_to_default_base_url() -> None:
    with patch("llm.factory.ChatOpenAI", return_value=MagicMock()) as chat_openai:
        _build_chat_model(
            model_type="opencode",
            model_name="deepseek-v4-flash-free",
            temperature=0.75,
            model_base_url="",
            model_api_key="public",
        )

    assert chat_openai.call_args.kwargs["base_url"] == _OPENCODE_DEFAULT_BASE_URL


def test_build_chat_model_opencode_returns_chat_openai_instance() -> None:
    with patch("llm.factory.ModelConfig") as model_config:
        model_config.max_retries = 2
        model_config.request_timeout = 30.0
        model = _build_chat_model(
            model_type="opencode",
            model_name="deepseek-v4-flash-free",
            temperature=0.75,
            model_base_url="https://opencode.ai/zen/v1",
            model_api_key="public",
        )

    assert isinstance(model, ChatOpenAI)


def test_build_chat_model_unsupported_type_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported MODEL_TYPE"):
        _build_chat_model(
            model_type="unknown-vendor",
            model_name="test",
            temperature=0.0,
            model_base_url="https://example.com/v1",
            model_api_key="key",
        )
