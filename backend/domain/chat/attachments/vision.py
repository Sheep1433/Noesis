"""Vision / multimodal 能力判定。"""

from __future__ import annotations

from typing import Optional

from config.env import ChatAttachmentConfig

_VISION_MODEL_HINTS = ("vl", "vision", "omni", "gpt-4o", "gemini")


def model_name_supports_vision(model_name: str) -> bool:
    """根据上游模型名判断是否支持原生 multimodal（image_url）。"""
    name = (model_name or "").lower()
    return any(hint in name for hint in _VISION_MODEL_HINTS)


def is_vision_available(model_id: Optional[str] = None) -> bool:
    """当前请求是否可对 LLM 直喂 image_url（受配置与 catalog model_name 约束）。"""
    if not ChatAttachmentConfig.vision_enabled:
        return False
    from llm.catalog import resolve_catalog_entry

    entry = resolve_catalog_entry(model_id)
    return model_name_supports_vision(entry.model_name)


def get_first_vision_catalog_id() -> Optional[str]:
    """catalog 中第一个支持 Vision 的 model id，供前端自动切换。"""
    from llm.catalog import get_model_catalog

    for entry in get_model_catalog():
        if model_name_supports_vision(entry.model_name):
            return entry.id
    return None


def resolve_effective_vision_model_id(model_id: Optional[str]) -> Optional[str]:
    """若当前 model 不支持 Vision，返回 catalog 中首个 Vision 模型 id；否则返回原 id。"""
    if is_vision_available(model_id):
        return model_id
    return get_first_vision_catalog_id()
