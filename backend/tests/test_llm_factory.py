from unittest.mock import MagicMock, patch

import pytest
from langchain_openai import ChatOpenAI

from noesis.llm.factory import (
    ChatOpenCode,
    _OPENCODE_DEFAULT_BASE_URL,
    _OPENCODE_DEFAULT_HEADERS,
    build_chat_model,
)


def test_build_chat_model_opencode_uses_required_headers() -> None:
    with patch(
        "noesis.llm.factory.ChatOpenCode",
        return_value=MagicMock(),
    ) as chat_opencode:
        build_chat_model(
            model_type="opencode",
            model_name="deepseek-v4-flash-free",
            temperature=0.75,
            model_base_url="https://opencode.ai/zen/v1",
            model_api_key="public",
        )

    chat_opencode.assert_called_once()
    kwargs = chat_opencode.call_args.kwargs
    assert kwargs["model"] == "deepseek-v4-flash-free"
    assert kwargs["base_url"] == "https://opencode.ai/zen/v1"
    assert kwargs["api_key"] == "public"
    assert kwargs["default_headers"] == _OPENCODE_DEFAULT_HEADERS
    assert kwargs["http_client"]._trust_env is False
    assert kwargs["http_async_client"]._trust_env is False


def test_llm_http_clients_bypass_system_proxy() -> None:
    from noesis.llm.factory import _llm_http_clients

    sync_client, async_client = _llm_http_clients()
    assert sync_client._trust_env is False
    assert async_client._trust_env is False


def test_build_chat_model_opencode_falls_back_to_default_base_url() -> None:
    with patch(
        "noesis.llm.factory.ChatOpenCode",
        return_value=MagicMock(),
    ) as chat_opencode:
        build_chat_model(
            model_type="opencode",
            model_name="deepseek-v4-flash-free",
            temperature=0.75,
            model_base_url="",
            model_api_key="public",
        )

    assert chat_opencode.call_args.kwargs["base_url"] == _OPENCODE_DEFAULT_BASE_URL


def test_build_chat_model_opencode_returns_chat_opencode_instance() -> None:
    with patch("noesis.llm.factory.ModelConfig") as model_config:
        model_config.max_retries = 2
        model_config.request_timeout = 30.0
        model_config.streaming = True
        model = build_chat_model(
            model_type="opencode",
            model_name="deepseek-v4-flash-free",
            temperature=0.75,
            model_base_url="https://opencode.ai/zen/v1",
            model_api_key="public",
        )

    assert isinstance(model, ChatOpenCode)
    assert isinstance(model, ChatOpenAI)


def test_build_chat_model_unsupported_type_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported MODEL_TYPE"):
        build_chat_model(
            model_type="unknown-vendor",
            model_name="test",
            temperature=0.0,
            model_base_url="https://example.com/v1",
            model_api_key="key",
        )


# === reasoning 字段归一化测试 ===


def test_extract_reasoning_deepseek_native() -> None:
    """DeepSeek 系列模型：delta.reasoning_content 字符串。"""
    delta = {"reasoning_content": "用户想查天气"}
    assert ChatOpenCode._extract_reasoning_from_delta(delta) == "用户想查天气"


def test_extract_reasoning_mimo_reasoning_field() -> None:
    """MiMo 系列：delta.reasoning 字符串 + delta.reasoning_details 数组。"""
    delta = {"reasoning": "用户", "reasoning_details": [{"type": "reasoning.text", "text": "用户"}]}
    # reasoning 字符串优先于 reasoning_details
    assert ChatOpenCode._extract_reasoning_from_delta(delta) == "用户"


def test_extract_reasoning_mimo_details_only() -> None:
    """MiMo 只有 reasoning_details 数组（无 reasoning 字符串）。"""
    delta = {"reasoning_details": [{"type": "reasoning.text", "text": "用户", "format": "unknown", "index": 0}]}
    assert ChatOpenCode._extract_reasoning_from_delta(delta) == "用户"


def test_extract_reasoning_mimo_details_multi_parts() -> None:
    """MiMo reasoning_details 多段拼接。"""
    delta = {"reasoning_details": [
        {"type": "reasoning.text", "text": "用户", "index": 0},
        {"type": "reasoning.text", "text": "询问", "index": 1},
    ]}
    assert ChatOpenCode._extract_reasoning_from_delta(delta) == "用户询问"


def test_extract_reasoning_none() -> None:
    """无 reasoning 字段。"""
    delta = {"content": "你好"}
    assert ChatOpenCode._extract_reasoning_from_delta(delta) is None


def test_extract_reasoning_empty_string() -> None:
    """reasoning_content 为空字符串（DeepSeek 初始化 chunk）。"""
    delta = {"reasoning_content": ""}
    assert ChatOpenCode._extract_reasoning_from_delta(delta) == ""
