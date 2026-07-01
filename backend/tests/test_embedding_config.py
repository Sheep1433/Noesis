"""Embedding / VLM 配置检测与回退策略。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kb.chunk import chunk
from kb.document_parse.parser import DocumentParser
from kb.embedding.embedding import (
    embedding_not_configured_message,
    get_embedding,
    is_embedding_configured,
    is_vlm_configured,
)


def _model_cfg(**overrides):
    base = dict(
        embedding_model_name="text-embedding-v4",
        embedding_model_base_url="https://example.com/v1",
        embedding_model_api_key="emb-key",
        vlm_model_name="Qwen3-VL-32B-Instruct",
        vlm_model_base_url="https://example.com/v1",
        vlm_model_api_key="vlm-key",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_is_embedding_configured_requires_all_fields() -> None:
    with patch("kb.embedding.embedding.ModelConfig", _model_cfg()):
        assert is_embedding_configured() is True

    with patch("kb.embedding.embedding.ModelConfig", _model_cfg(embedding_model_api_key="")):
        assert is_embedding_configured() is False


def test_is_vlm_configured_requires_all_fields() -> None:
    with patch("kb.embedding.embedding.ModelConfig", _model_cfg()):
        assert is_vlm_configured() is True

    with patch("kb.embedding.embedding.ModelConfig", _model_cfg(vlm_model_api_key="")):
        assert is_vlm_configured() is False


def test_get_embedding_raises_when_not_configured() -> None:
    with patch("kb.embedding.embedding.ModelConfig", _model_cfg(embedding_model_api_key="")):
        with pytest.raises(ValueError, match="未配置 Embedding"):
            get_embedding()


def test_get_embedding_dashscope_compat_kwargs() -> None:
    with patch("kb.embedding.embedding.ModelConfig", _model_cfg()):
        with patch("langchain_openai.OpenAIEmbeddings") as mock_cls:
            mock_cls.return_value = object()
            get_embedding()
    mock_cls.assert_called_once_with(
        model="text-embedding-v4",
        openai_api_key="emb-key",
        openai_api_base="https://example.com/v1",
        check_embedding_ctx_length=False,
    )


def test_get_embedding_dashscope_batch_size() -> None:
    with patch(
        "kb.embedding.embedding.ModelConfig",
        _model_cfg(embedding_model_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ):
        with patch("langchain_openai.OpenAIEmbeddings") as mock_cls:
            mock_cls.return_value = object()
            get_embedding()
    mock_cls.assert_called_once_with(
        model="text-embedding-v4",
        openai_api_key="emb-key",
        openai_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        check_embedding_ctx_length=False,
        chunk_size=10,
    )


def test_chunk_warns_when_embedding_not_configured() -> None:
    with (
        patch("kb.chunk.chunker.is_embedding_configured", return_value=False),
        patch("kb.chunk.chunker.logger.warning") as warn,
    ):
        docs = chunk("# Title\n\nhello", effective_params={"chunk_size": 200, "chunk_overlap": 0})

    assert docs
    warn.assert_called_once_with(embedding_not_configured_message())


def test_replace_images_skips_silently_when_vlm_not_configured() -> None:
    md = "![alt](data:image/png;base64,QUJD)"
    with patch("kb.embedding.is_vlm_configured", return_value=False):
        assert DocumentParser._replace_images_with_descriptions(md) == md
