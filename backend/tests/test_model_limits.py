"""Per-model limit.context from catalog config."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from config.yaml_config import AppYamlConfig, ModelCatalogEntryYamlSection, ModelLimitYamlSection, ModelYamlSection
from llm.catalog import ModelCatalogEntry, get_model_catalog, resolve_catalog_entry
from llm.model_limits import DEFAULT_CONTEXT_TOKENS, resolve_context_max_tokens, resolve_model_limit


def _yaml_with_catalog() -> AppYamlConfig:
    return AppYamlConfig(
        model=ModelYamlSection(
            type="opencode",
            name="deepseek-v4-flash-free",
            base_url="https://opencode.ai/zen/v1",
            default_catalog_id="flash",
            catalog=[
                ModelCatalogEntryYamlSection(
                    id="flash",
                    label="Flash",
                    name="deepseek-v4-flash-free",
                    limit=ModelLimitYamlSection(context=200000, output=128000),
                ),
                ModelCatalogEntryYamlSection(
                    id="nemotron",
                    label="Nemotron",
                    name="nemotron-3-ultra-free",
                    limit=ModelLimitYamlSection(context=1_000_000, output=128000),
                ),
            ],
        )
    )


@patch("llm.catalog.load_app_yaml")
def test_resolve_context_max_tokens_from_catalog_limit(mock_load_yaml) -> None:
    mock_load_yaml.return_value = _yaml_with_catalog()
    get_model_catalog.cache_clear()

    assert resolve_context_max_tokens("flash") == 200_000
    assert resolve_context_max_tokens("nemotron") == 1_000_000

    get_model_catalog.cache_clear()


@patch("llm.catalog.resolve_catalog_entry")
def test_resolve_context_max_tokens_falls_back_to_global(mock_resolve) -> None:
    mock_resolve.return_value = ModelCatalogEntry(
        id="plain",
        label="Plain",
        model_type="qwen",
        model_name="qwen-plus",
        temperature=0.7,
        base_url="https://example.com/v1",
        limit=None,
    )
    cfg = SimpleNamespace(context_max_input_tokens=64000)
    with patch("llm.model_limits.ModelConfig", cfg):
        assert resolve_context_max_tokens("plain") == 64000


@patch("llm.catalog.resolve_catalog_entry")
def test_resolve_context_max_tokens_default_when_unset(mock_resolve) -> None:
    mock_resolve.return_value = ModelCatalogEntry(
        id="plain",
        label="Plain",
        model_type="qwen",
        model_name="qwen-plus",
        temperature=0.7,
        base_url="https://example.com/v1",
        limit=None,
    )
    cfg = SimpleNamespace(context_max_input_tokens=0)
    with patch("llm.model_limits.ModelConfig", cfg):
        assert resolve_context_max_tokens("plain") == DEFAULT_CONTEXT_TOKENS


@patch("llm.catalog.load_app_yaml")
def test_catalog_entry_inherits_model_level_limit(mock_load_yaml) -> None:
    mock_load_yaml.return_value = AppYamlConfig(
        model=ModelYamlSection(
            type="qwen",
            name="qwen-plus",
            limit=ModelLimitYamlSection(context=129024, output=8192),
            catalog=[
                ModelCatalogEntryYamlSection(id="default", label="Plus", name="qwen-plus"),
            ],
        )
    )
    get_model_catalog.cache_clear()

    entry = resolve_catalog_entry("default")
    assert entry.limit is not None
    assert entry.limit.context == 129024
    assert entry.limit.output == 8192

    get_model_catalog.cache_clear()


@patch("llm.catalog.load_app_yaml")
def test_resolve_model_limit_prefers_catalog_over_global(mock_load_yaml) -> None:
    mock_load_yaml.return_value = _yaml_with_catalog()
    get_model_catalog.cache_clear()

    cfg = SimpleNamespace(context_max_input_tokens=128000)
    with patch("llm.model_limits.ModelConfig", cfg):
        limit = resolve_model_limit("flash")

    assert limit.context == 200_000
    assert limit.output == 128_000

    get_model_catalog.cache_clear()
