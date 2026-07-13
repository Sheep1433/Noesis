"""对话模型目录：从 config.yaml 加载可选模型，供运行时切换。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, List, Optional

from config.env import ModelConfig
from config.yaml_config import (
    ModelCatalogEntryYamlSection,
    ModelLimitYamlSection,
    load_app_yaml,
)
from llm.model_limits import ModelLimit


@dataclass(frozen=True)
class ModelCatalogEntry:
    id: str
    label: str
    model_type: str
    model_name: str
    temperature: float
    base_url: str
    is_default: bool = False
    limit: ModelLimit | None = None


def _limit_from_yaml(raw: ModelLimitYamlSection | None) -> ModelLimit | None:
    if raw is None or raw.context <= 0:
        return None
    return ModelLimit(
        context=int(raw.context),
        output=int(raw.output) if raw.output is not None else None,
        input=int(raw.input) if raw.input is not None else None,
    )


def _entry_from_yaml(
    raw: ModelCatalogEntryYamlSection,
    *,
    default_type: str,
    default_name: str,
    default_temperature: float,
    default_base_url: str,
    default_limit: ModelLimit | None,
    is_default: bool,
) -> ModelCatalogEntry:
    model_id = str(raw.id or "").strip()
    label = str(raw.label or "").strip() or model_id
    model_type = str(raw.type or default_type).strip().lower()
    model_name = str(raw.name or default_name).strip()
    temperature = float(raw.temperature if raw.temperature is not None else default_temperature)
    base_url = str(raw.base_url or default_base_url).strip()
    limit = _limit_from_yaml(raw.limit) or default_limit
    return ModelCatalogEntry(
        id=model_id,
        label=label,
        model_type=model_type,
        model_name=model_name,
        temperature=temperature,
        base_url=base_url,
        is_default=is_default,
        limit=limit,
    )


@lru_cache
def get_model_catalog() -> tuple[ModelCatalogEntry, ...]:
    yaml_cfg = load_app_yaml()
    m = yaml_cfg.model
    default_type = str(m.type or ModelConfig.model_type).strip().lower()
    default_name = str(m.name or ModelConfig.model_name).strip()
    default_temperature = float(m.temperature)
    default_base_url = str(m.base_url or ModelConfig.model_base_url).strip()
    default_limit = _limit_from_yaml(m.limit)

    raw_entries = list(m.catalog or [])
    if not raw_entries:
        return (
            ModelCatalogEntry(
                id="default",
                label=default_name,
                model_type=default_type,
                model_name=default_name,
                temperature=default_temperature,
                base_url=default_base_url,
                is_default=True,
                limit=default_limit,
            ),
        )

    entries: List[ModelCatalogEntry] = []
    default_id = str(m.default_catalog_id or "").strip()
    seen: set[str] = set()
    for idx, raw in enumerate(raw_entries):
        model_id = str(raw.id or "").strip()
        if not model_id:
            model_id = "default" if idx == 0 else f"model-{idx + 1}"
        if model_id in seen:
            continue
        seen.add(model_id)
        is_default = model_id == default_id if default_id else idx == 0
        entries.append(
            _entry_from_yaml(
                raw,
                default_type=default_type,
                default_name=default_name,
                default_temperature=default_temperature,
                default_base_url=default_base_url,
                default_limit=default_limit,
                is_default=is_default,
            )
        )

    if entries and not any(e.is_default for e in entries):
        first = entries[0]
        entries[0] = ModelCatalogEntry(
            id=first.id,
            label=first.label,
            model_type=first.model_type,
            model_name=first.model_name,
            temperature=first.temperature,
            base_url=first.base_url,
            is_default=True,
            limit=first.limit,
        )
    return tuple(entries)


def get_default_model_id() -> str:
    for entry in get_model_catalog():
        if entry.is_default:
            return entry.id
    return get_model_catalog()[0].id


def resolve_catalog_entry(model_id: Optional[str]) -> ModelCatalogEntry:
    catalog = get_model_catalog()
    normalized = str(model_id or "").strip()
    if normalized:
        for entry in catalog:
            if entry.id == normalized:
                return entry
    for entry in catalog:
        if entry.is_default:
            return entry
    return catalog[0]


def list_public_models() -> List[dict[str, Any]]:
    from domain.chat.attachments.vision import model_name_supports_vision
    from llm.model_limits import resolve_model_limit

    default_id = get_default_model_id()
    rows: List[dict[str, Any]] = []
    for entry in get_model_catalog():
        limit = resolve_model_limit(entry.id)
        row: dict[str, Any] = {
            "id": entry.id,
            "label": entry.label,
            "model_name": entry.model_name,
            "model_type": entry.model_type,
            "is_default": entry.id == default_id,
            "supports_vision": model_name_supports_vision(entry.model_name),
            "limit": limit.as_dict(),
        }
        rows.append(row)
    return rows


def get_catalog_vision_meta() -> dict[str, Any]:
    from domain.chat.attachments.vision import get_first_vision_catalog_id
    from kb.embedding import is_vlm_configured

    return {
        "first_vision_model_id": get_first_vision_catalog_id(),
        "vlm_fallback_available": is_vlm_configured(),
    }
