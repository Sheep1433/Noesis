"""VLM caption 单测。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from domain.chat.attachments.vlm_caption import (
    _empty_response_error,
    _extract_message_text,
    describe_image_bytes_for_chat,
)


def test_extract_message_text_prefers_content() -> None:
    msg = SimpleNamespace(content="主内容", reasoning_content="思考")
    assert _extract_message_text(msg) == "主内容"


def test_extract_message_text_falls_back_to_reasoning() -> None:
    msg = SimpleNamespace(content="", reasoning_content="  思考结果  ")
    assert _extract_message_text(msg) == "思考结果"


def test_empty_response_error_message() -> None:
    err = _empty_response_error("qwen-vl-max", "stop")
    assert "qwen-vl-max" in str(err)
    assert "finish_reason" in str(err)


@patch("kb.embedding.is_vlm_configured", return_value=True)
@patch("config.env.ModelConfig")
def test_describe_image_uses_configured_model_only(mock_cfg, _mock_vlm) -> None:
    mock_cfg.vlm_model_api_key = "key"
    mock_cfg.vlm_model_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    mock_cfg.vlm_model_name = "qwen-vl-max"

    client = MagicMock()
    ok_resp = MagicMock()
    ok_resp.choices = [MagicMock()]
    ok_resp.choices[0].message = SimpleNamespace(content="红色方块", reasoning_content="")
    ok_resp.choices[0].finish_reason = "stop"
    client.chat.completions.create.return_value = ok_resp

    with patch("openai.OpenAI", return_value=client):
        text = describe_image_bytes_for_chat(b"jpeg", "image/jpeg", file_name="a.png")

    assert text == "红色方块"
    assert client.chat.completions.create.call_count == 1
    assert client.chat.completions.create.call_args.kwargs["model"] == "qwen-vl-max"


@patch("kb.embedding.is_vlm_configured", return_value=True)
@patch("config.env.ModelConfig")
def test_describe_image_raises_on_empty_content(mock_cfg, _mock_vlm) -> None:
    mock_cfg.vlm_model_api_key = "key"
    mock_cfg.vlm_model_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    mock_cfg.vlm_model_name = "qwen-image-2.0-pro-2026-04-22"

    client = MagicMock()
    empty_resp = MagicMock()
    empty_resp.choices = [MagicMock()]
    empty_resp.choices[0].message = SimpleNamespace(content=None, reasoning_content=None)
    empty_resp.choices[0].finish_reason = "stop"
    client.chat.completions.create.return_value = empty_resp

    with patch("openai.OpenAI", return_value=client):
        with pytest.raises(ValueError, match="VLM 返回空描述"):
            describe_image_bytes_for_chat(b"jpeg", "image/jpeg", file_name="a.png")
