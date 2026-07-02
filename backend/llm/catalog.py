"""对话模型目录：从 config.yaml 加载可选模型，供运行时切换。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, List, Optional

from config.env import ModelConfig
from config.yaml_config import AppYamlConfig, ModelCatalogEntryYamlSection, load_app_yaml


@dataclass(frozen=True)
class ModelCatalogEntry:
    id: str
    label: str
    model_type: str
    model_name: str
    temperature: float
    base_url: str
    is_default: bool = False


def _entry_from_yaml(
    raw: ModelCatalogEntryYamlSection,
    *,
    default_type: str,
    default_name: str,
    default_temperature: float,
    default_base_url: str,
    is_default: bool,
) -> ModelCatalogEntry:
    model_id = str(raw.id or "").strip()
    label = str(raw.label or "").strip() or model_id
    model_type = str(raw.type or default_type).strip().lower()
    model_name = str(raw.name or default_name).strip()
    temperature = float(raw.temperature if raw.temperature is not None else default_temperature)
    base_url = str(raw.base_url or default_base_url).strip()
    return ModelCatalogEntry(
        id=model_id,
        label=label,
        model_type=model_type,
        model_name=model_name,
        temperature=temperature,
        base_url=base_url,
        is_default=is_default,
    )


@lru_cache
def get_model_catalog() -> tuple[ModelCatalogEntry, ...]:
    yaml_cfg = load_app_yaml()
    m = yaml_cfg.model
    default_type = str(m.type or ModelConfig.model_type).strip().lower()
    default_name = str(m.name or ModelConfig.model_name).strip()
    default_temperature = float(m.temperature)
    default_base_url = str(m.base_url or ModelConfig.model_base_url).strip()

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
                is_default=is_default,
            )
        )

    if entries and not any(e.is_default for e in entries):
        entries[0] = ModelCatalogEntry(
            id=entries[0].id,
            label=entries[0].label,
            model_type=entries[0].model_type,
            model_name=entries[0].model_name,
            temperature=entries[0].temperature,
            base_url=entries[0].base_url,
            is_default=True,
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
    default_id = get_default_model_id()
    return [
        {
            "id": entry.id,
            "label": entry.label,
            "model_name": entry.model_name,
            "model_type": entry.model_type,
            "is_default": entry.id == default_id,
        }
        for entry in get_model_catalog()
    ]
