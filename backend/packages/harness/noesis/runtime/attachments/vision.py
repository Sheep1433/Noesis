"""Vision / multimodal 能力判定。"""

from __future__ import annotations

from typing import Optional

from noesis.config.env import ChatAttachmentConfig
from noesis.llm.catalog import resolve_catalog_entry
from noesis.llm.vision_meta import get_first_vision_catalog_id, model_name_supports_vision

__all__ = [
    "model_name_supports_vision",
    "is_vision_available",
    "get_first_vision_catalog_id",
    "resolve_effective_vision_model_id",
]


def is_vision_available(model_id: Optional[str] = None) -> bool:
    """当前请求是否可对 LLM 直喂 image_url（受配置与 catalog model_name 约束）。"""
    if not ChatAttachmentConfig.vision_enabled:
        return False
    entry = resolve_catalog_entry(model_id)
    return model_name_supports_vision(entry.model_name)


def resolve_effective_vision_model_id(model_id: Optional[str]) -> Optional[str]:
    """若当前 model 不支持 Vision，返回 catalog 中首个 Vision 模型 id；否则返回原 id。"""
    if is_vision_available(model_id):
        return model_id
    return get_first_vision_catalog_id()
