"""模型目录与 get_llm(model_id) 单测。"""

from unittest.mock import patch
from types import SimpleNamespace

from noesis.llm.catalog import (
    get_default_model_id,
    get_model_catalog,
    list_public_models,
    resolve_catalog_entry,
)
from noesis.llm.factory import get_llm


@patch("noesis.llm.catalog.load_app_yaml")
def test_model_catalog_uses_yaml_entries(mock_load_yaml):
    from noesis.config.yaml_config import AppYamlConfig, ModelCatalogEntryYamlSection, ModelYamlSection

    mock_load_yaml.return_value = AppYamlConfig(
        model=ModelYamlSection(
            type="opencode",
            name="deepseek-v4-flash-free",
            base_url="https://opencode.ai/zen/v1",
            default_catalog_id="deepseek-v4-flash-free",
            catalog=[
                ModelCatalogEntryYamlSection(id="deepseek-v4-flash-free", label="Flash"),
                ModelCatalogEntryYamlSection(
                    id="deepseek-reasoner",
                    label="Reasoner",
                    context_window=200000,
                ),
            ],
        )
    )
    get_model_catalog.cache_clear()

    catalog = get_model_catalog()
    assert len(catalog) == 2
    assert get_default_model_id() == "deepseek-v4-flash-free"
    assert resolve_catalog_entry("deepseek-reasoner").id == "deepseek-reasoner"
    assert resolve_catalog_entry("deepseek-reasoner").context_window == 200_000
    assert resolve_catalog_entry(None).id == "deepseek-v4-flash-free"

    get_model_catalog.cache_clear()


@patch("noesis.llm.factory.build_chat_model")
@patch("noesis.llm.catalog.resolve_catalog_entry")
def test_get_llm_accepts_model_id(mock_resolve, mock_build):
    from noesis.llm.catalog import ModelCatalogEntry

    mock_resolve.return_value = ModelCatalogEntry(
        id="deepseek-reasoner",
        label="Reasoner",
        model_type="deepseek",
        temperature=0.6,
        base_url="https://example.com/v1",
    )
    mock_build.return_value = object()

    with patch(
        "noesis.llm.factory.ModelConfig",
        SimpleNamespace(model_api_key="test-key", summarization_model_name=""),
    ):
        get_llm(model_id="deepseek-reasoner")

    mock_build.assert_called_once()
    kwargs = mock_build.call_args.kwargs
    assert kwargs["model_type"] == "deepseek"
    assert kwargs["model_name"] == "deepseek-reasoner"
    assert kwargs["temperature"] == 0.6
    assert kwargs["model_base_url"] == "https://example.com/v1"

