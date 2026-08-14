"""Per-model context window from config.yaml catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from noesis.config.env import ModelConfig
from noesis.runtime.logging import logger

DEFAULT_CONTEXT_TOKENS = 128_000


@dataclass(frozen=True)
class ModelContextWindow:
    """模型上下文窗口上限（圆环分母 / 压缩阈值）。"""

    context: int


def resolve_model_context_window(model_id: Optional[str] = None) -> ModelContextWindow:
    """解析 catalog 模型的上下文窗口。"""
    from noesis.llm.catalog import resolve_catalog_entry

    entry = resolve_catalog_entry(model_id)
    if entry.context_window > 0:
        return ModelContextWindow(context=entry.context_window)

    if ModelConfig.context_max_input_tokens > 0:
        return ModelContextWindow(context=int(ModelConfig.context_max_input_tokens))

    logger.warning(
        "模型 {} 未配置 context_window，且 context.max_input_tokens=0，使用默认值 {}",
        entry.id,
        DEFAULT_CONTEXT_TOKENS,
    )
    return ModelContextWindow(context=DEFAULT_CONTEXT_TOKENS)


def resolve_context_max_tokens(model_id: Optional[str] = None) -> int:
    """Context occupancy denominator (``context_window``)."""
    return resolve_model_context_window(model_id).context
