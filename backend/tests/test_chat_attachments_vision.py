"""Vision 判定与图片预处理单测。"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from domain.chat.attachments.image_prepare import prepare_image_bytes_for_injection
from domain.chat.attachments.vision import (
    is_vision_available,
    model_name_supports_vision,
    resolve_effective_vision_model_id,
)
from llm.catalog import ModelCatalogEntry


def test_model_name_supports_vision() -> None:
    assert model_name_supports_vision("qwen-vl-max") is True
    assert model_name_supports_vision("gpt-4o-mini") is True
    assert model_name_supports_vision("qwen-plus") is False


@patch("domain.chat.attachments.vision.ChatAttachmentConfig")
@patch("llm.catalog.resolve_catalog_entry")
def test_is_vision_available_uses_model_id(mock_resolve, mock_cfg) -> None:
    mock_cfg.vision_enabled = True
    mock_resolve.return_value = ModelCatalogEntry(
        id="vl",
        label="VL",
        model_type="qwen",
        model_name="qwen-vl-max",
        temperature=0.7,
        base_url="http://example",
        is_default=False,
    )
    assert is_vision_available("vl") is True
    mock_resolve.assert_called_once_with("vl")


@patch("domain.chat.attachments.vision.get_first_vision_catalog_id", return_value="qwen-vl-max")
@patch("domain.chat.attachments.vision.is_vision_available")
def test_resolve_effective_vision_model_id_switches(mock_avail, _mock_first) -> None:
    mock_avail.side_effect = lambda model_id=None: model_id == "qwen-vl-max"
    assert resolve_effective_vision_model_id("qwen-plus") == "qwen-vl-max"
    assert resolve_effective_vision_model_id("qwen-vl-max") == "qwen-vl-max"


def test_prepare_image_bytes_small_passthrough() -> None:
    pytest.importorskip("PIL")
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (8, 8), color=(255, 0, 0)).save(buf, format="PNG")
    data, mime = prepare_image_bytes_for_injection(buf.getvalue(), "image/png", max_edge=1536)
    assert mime == "image/jpeg"
    assert data
