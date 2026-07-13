"""Per-model context limits from config.yaml catalog (models.dev / OpenCode shape)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from config.env import ModelConfig
from common.logging import logger

DEFAULT_CONTEXT_TOKENS = 128_000


@dataclass(frozen=True)
class ModelLimit:
    """Aligns with models.dev / OpenCode ``limit`` object."""

    context: int
    output: int | None = None
    input: int | None = None

    def as_dict(self) -> dict[str, int | None]:
        payload: dict[str, int | None] = {"context": self.context}
        if self.output is not None:
            payload["output"] = self.output
        if self.input is not None:
            payload["input"] = self.input
        return payload


def resolve_model_limit(model_id: Optional[str] = None) -> ModelLimit:
    """Resolve effective context window for a catalog model id."""
    from llm.catalog import resolve_catalog_entry

    entry = resolve_catalog_entry(model_id)
    if entry.limit is not None and entry.limit.context > 0:
        return entry.limit

    if ModelConfig.context_max_input_tokens > 0:
        return ModelLimit(context=int(ModelConfig.context_max_input_tokens))

    logger.warning(
        "模型 {} 未配置 limit.context，且 context.max_input_tokens=0，使用默认值 {}",
        entry.id,
        DEFAULT_CONTEXT_TOKENS,
    )
    return ModelLimit(context=DEFAULT_CONTEXT_TOKENS)


def resolve_context_max_tokens(model_id: Optional[str] = None) -> int:
    """Context occupancy denominator (``limit.context``)."""
    return resolve_model_limit(model_id).context
