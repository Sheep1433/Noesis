"""嵌入层：文本 → 向量。"""
from __future__ import annotations

from kb.embedding.embedding import (
    embedding_not_configured_message,
    get_embedding,
    is_embedding_configured,
    is_vlm_configured,
)

__all__ = [
    "embedding_not_configured_message",
    "get_embedding",
    "is_embedding_configured",
    "is_vlm_configured",
]
