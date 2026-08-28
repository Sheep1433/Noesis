"""对话模型目录：从 config.yaml 加载可选模型，供运行时切换。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, List, Optional

from noesis.config.env import ModelConfig
from noesis.config.yaml_config import (
    ModelCatalogEntryYamlSection,
    load_app_yaml,
)


@dataclass(frozen=True)
class ModelCatalogEntry:
    id: str
    label: str
    model_type: str
    temperature: float
    base_url: str
    is_default: bool = False
    context_window: int = 0


def _entry_from_yaml(
    raw: ModelCatalogEntryYamlSection,
    *,
    default_type: str,
    default_name: str,
    default_temperature: float,
    default_base_url: str,
    default_context_window: int,
    is_default: bool,
) -> ModelCatalogEntry:
    model_id = str(raw.id or default_name).strip()
    label = str(raw.label or "").strip() or model_id
    model_type = str(raw.type or default_type).strip().lower()
    temperature = float(raw.temperature if raw.temperature is not None else default_temperature)
    base_url = str(raw.base_url or default_base_url).strip()
    context_window = int(raw.context_window or default_context_window)
    return ModelCatalogEntry(
        id=model_id,
        label=label,
        model_type=model_type,
        temperature=temperature,
        base_url=base_url,
        is_default=is_default,
        context_window=context_window,
    )


@lru_cache
def get_model_catalog() -> tuple[ModelCatalogEntry, ...]:
    yaml_cfg = load_app_yaml()
    m = yaml_cfg.model
    default_type = str(m.type or ModelConfig.model_type).strip().lower()
    default_name = str(m.name or ModelConfig.model_name).strip()
    default_temperature = float(m.temperature)
    default_base_url = str(m.base_url or ModelConfig.model_base_url).strip()
    default_context_window = int(m.context_window or 0)

    raw_entries = list(m.catalog or [])
    if not raw_entries:
        return (
            ModelCatalogEntry(
                id=default_name,
                label=default_name,
                model_type=default_type,
                temperature=default_temperature,
                base_url=default_base_url,
                is_default=True,
                context_window=default_context_window,
            ),
        )

    entries: List[ModelCatalogEntry] = []
    default_id = str(m.default_catalog_id or "").strip()
    seen: set[str] = set()
    for idx, raw in enumerate(raw_entries):
        model_id = str(raw.id or "").strip()
        if not model_id:
            model_id = default_name if idx == 0 else f"model-{idx + 1}"
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
                default_context_window=default_context_window,
                is_default=is_default,
            )
        )

    if entries and not any(e.is_default for e in entries):
        first = entries[0]
        entries[0] = ModelCatalogEntry(
            id=first.id,
            label=first.label,
            model_type=first.model_type,
            temperature=first.temperature,
            base_url=first.base_url,
            is_default=True,
            context_window=first.context_window,
        )
    return tuple(entries)


def provider_display_label(model_type: str, base_url: str) -> str:
    """Provider 展示标签：预设名优先，无预设回退端点域名（去 www./api. 前缀）。

    model_type 是协议选择器（openai/qwen/...），不是厂商名——
    默认端点无对应预设（如 kilo 走 openai 协议）时以域名展示，避免裸协议名误导。
    """
    from urllib.parse import urlparse

    from noesis.config.env import ModelConfig

    for preset in ModelConfig.provider_presets:
        if preset.get("id") == model_type:
            return str(preset.get("label") or preset.get("id"))
    host = urlparse(base_url).hostname or ""
    host = host.removeprefix("www.").removeprefix("api.")
    return host or model_type


def get_default_model_id() -> str:
    for entry in get_model_catalog():
        if entry.is_default:
            return entry.id
    return get_model_catalog()[0].id


def resolve_catalog_entry(model_id: Optional[str]) -> ModelCatalogEntry:
    from noesis.llm.runtime_snapshot import get_runtime_model_snapshot

    snapshot = get_runtime_model_snapshot(model_id)
    if snapshot is not None:
        return ModelCatalogEntry(
            id=snapshot.id,
            label=snapshot.label or snapshot.id,
            model_type=snapshot.model_type,
            temperature=float(ModelConfig.model_temperature),
            base_url=snapshot.base_url,
            is_default=False,
            context_window=snapshot.context_window,
        )
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
    from noesis.llm.vision_meta import model_name_supports_vision

    default_id = get_default_model_id()
    rows: List[dict[str, Any]] = []
    for entry in get_model_catalog():
        row: dict[str, Any] = {
            "id": entry.id,
            "label": entry.label,
            "provider": provider_display_label(entry.model_type, entry.base_url),
            "model_type": entry.model_type,
            "is_default": entry.id == default_id,
            "supports_vision": model_name_supports_vision(entry.id),
            "context_window": entry.context_window,
        }
        rows.append(row)
    return rows


def get_catalog_vision_meta() -> dict[str, Any]:
    from noesis.knowledge.embedding import is_vlm_configured
    from noesis.llm.vision_meta import get_first_vision_catalog_id

    # VLM fallback 探测属平台能力；经 deps 绑定，避免 noesis→kb 反向依赖。
    try:
        vlm_fallback = is_vlm_configured()
    except RuntimeError:
        vlm_fallback = False

    return {
        "first_vision_model_id": get_first_vision_catalog_id(),
        "vlm_fallback_available": vlm_fallback,
    }
