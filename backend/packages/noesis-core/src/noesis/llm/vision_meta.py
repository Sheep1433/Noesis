"""Catalog-side vision capability helpers (no harness dependency)."""

from __future__ import annotations

from typing import Optional

_VISION_MODEL_HINTS = ("vl", "vision", "omni", "gpt-4o", "gemini")


def model_name_supports_vision(model_name: str) -> bool:
    """根据上游模型名判断是否支持原生 multimodal（image_url）。"""
    name = (model_name or "").lower()
    return any(hint in name for hint in _VISION_MODEL_HINTS)


def get_first_vision_catalog_id() -> Optional[str]:
    """catalog 中第一个支持 Vision 的 model id，供前端自动切换。"""
    from noesis.llm.catalog import get_model_catalog

    for entry in get_model_catalog():
        if model_name_supports_vision(entry.model_name):
            return entry.id
    return None
