"""Vision / multimodal 能力判定。"""

from __future__ import annotations

from config.env import ChatAttachmentConfig, ModelConfig

_VISION_MODEL_HINTS = ("vl", "vision", "omni", "gpt-4o", "gemini")


def is_vision_available() -> bool:
    if not ChatAttachmentConfig.vision_enabled:
        return False
    name = (ModelConfig.model_name or "").lower()
    return any(hint in name for hint in _VISION_MODEL_HINTS)
