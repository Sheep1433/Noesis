"""Summarization offload factory：关闭 / 独立模型 / 回退主模型。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.middlewares.summary_offload_middleware import (
    SummarizationOffloadMiddleware,
    create_summary_offload_middleware,
)


def test_create_summary_offload_disabled() -> None:
    cfg = SimpleNamespace(summarization_enabled=False)
    with patch("agent.middlewares.summary_offload_middleware.ModelConfig", cfg):
        assert create_summary_offload_middleware() is None


def test_create_summary_offload_enabled_calls_summarization_llm() -> None:
    cfg = SimpleNamespace(
        summarization_enabled=True,
        context_max_input_tokens=8000,
        summarization_max_input_tokens=0,
        summarization_trigger_tokens=50000,
        summarization_trigger_fraction=0.85,
        summarization_messages_to_keep=6,
        summarization_tool_offload_threshold=1000,
        summarization_max_retention_ratio=0.6,
        max_tokens=4096,
    )
    mock_model = MagicMock()
    mock_model.profile = None

    with (
        patch("agent.middlewares.summary_offload_middleware.ModelConfig", cfg),
        patch("agent.middlewares.context_metrics.ModelConfig", cfg),
        patch(
            "agent.middlewares.summary_offload_middleware.get_llm",
            return_value=mock_model,
        ) as get_llm,
    ):
        mw = create_summary_offload_middleware()

    get_llm.assert_called_once_with(purpose="summarization")
    assert isinstance(mw, SummarizationOffloadMiddleware)
    assert mock_model.profile == {"max_input_tokens": 8000}
    assert mw._get_token_trigger_value() == 50000


def test_get_llm_summarization_falls_back_to_main_model() -> None:
    """未配置独立摘要模型名时，purpose=summarization 使用主模型参数。"""
    cfg = SimpleNamespace(
        summarization_model_name="",
        model_type="openai",
        model_name="qwen-main",
        model_temperature="0.2",
        model_api_key="main-key",
        model_base_url="https://main.example/v1",
    )
    with (
        patch("llm.factory.ModelConfig", cfg),
        patch("llm.factory._build_chat_model", return_value=MagicMock()) as build,
    ):
        from llm.factory import get_llm

        get_llm(purpose="summarization")

    kwargs = build.call_args.kwargs
    assert kwargs["model_name"] == "qwen-main"
    assert kwargs["model_api_key"] == "main-key"


def test_get_llm_summarization_uses_dedicated_model_name() -> None:
    """摘要仅换模型名；type / base_url / api_key 与主模型一致。"""
    cfg = SimpleNamespace(
        summarization_model_name="qwen-summary",
        summarization_model_temperature=0.1,
        model_type="openai",
        model_name="qwen-main",
        model_temperature="0.2",
        model_api_key="main-key",
        model_base_url="https://main.example/v1",
    )
    with (
        patch("llm.factory.ModelConfig", cfg),
        patch("llm.factory._build_chat_model", return_value=MagicMock()) as build,
    ):
        from llm.factory import get_llm

        get_llm(purpose="summarization")

    kwargs = build.call_args.kwargs
    assert kwargs["model_name"] == "qwen-summary"
    assert kwargs["model_api_key"] == "main-key"
    assert kwargs["model_base_url"] == "https://main.example/v1"
    assert kwargs["model_type"] == "openai"
