"""Structured citation feature gate shared by citation-capable agents."""

from __future__ import annotations

from typing import Optional

from noesis.config.env import ModelConfig
from noesis.llm.catalog import resolve_catalog_entry


def structured_citations_enabled(model_id: Optional[str]) -> bool:
    if not ModelConfig.structured_citations_enabled:
        return False
    if model_id:
        try:
            model_name = resolve_catalog_entry(model_id).model_name
        except (KeyError, ValueError):
            return False
    else:
        model_name = ModelConfig.model_name
    return model_name in ModelConfig.structured_citation_models
