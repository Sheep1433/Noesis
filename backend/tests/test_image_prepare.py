"""图片预览与注入压缩单测。"""
from __future__ import annotations

from io import BytesIO

from PIL import Image

from domain.chat.attachments.image_prepare import (
    _PREVIEW_MAX_BASE64_LEN,
    build_image_preview_base64,
)


def _png_bytes(width: int, height: int) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), (120, 80, 200)).save(buf, format="PNG")
    return buf.getvalue()


def test_build_image_preview_base64_fits_text_column() -> None:
    data = _png_bytes(478, 980)
    preview = build_image_preview_base64(data, "image/png")
    assert preview is not None
    assert len(preview) <= _PREVIEW_MAX_BASE64_LEN


def test_build_image_preview_base64_large_image_still_bounded() -> None:
    data = _png_bytes(2000, 3000)
    preview = build_image_preview_base64(data, "image/png")
    assert preview is not None
    assert len(preview) <= _PREVIEW_MAX_BASE64_LEN
