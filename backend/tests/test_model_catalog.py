"""模型目录与 get_llm(model_id) 单测。"""

from unittest.mock import patch

from llm.catalog import get_default_model_id, get_model_catalog, resolve_catalog_entry
from llm.factory import get_llm


@patch("llm.catalog.load_app_yaml")
def test_model_catalog_uses_yaml_entries(mock_load_yaml):
    from config.yaml_config import AppYamlConfig, ModelCatalogEntryYamlSection, ModelYamlSection

    mock_load_yaml.return_value = AppYamlConfig(
        model=ModelYamlSection(
            type="opencode",
            name="deepseek-v4-flash-free",
            base_url="https://opencode.ai/zen/v1",
            default_catalog_id="flash",
            catalog=[
                ModelCatalogEntryYamlSection(id="flash", label="Flash", name="deepseek-v4-flash-free"),
                ModelCatalogEntryYamlSection(id="reasoner", label="Reasoner", name="deepseek-reasoner"),
            ],
        )
    )
    get_model_catalog.cache_clear()

    catalog = get_model_catalog()
    assert len(catalog) == 2
    assert get_default_model_id() == "flash"
    assert resolve_catalog_entry("reasoner").model_name == "deepseek-reasoner"
    assert resolve_catalog_entry(None).id == "flash"

    get_model_catalog.cache_clear()


@patch("llm.factory._build_chat_model")
@patch("llm.catalog.resolve_catalog_entry")
def test_get_llm_accepts_model_id(mock_resolve, mock_build):
    from llm.catalog import ModelCatalogEntry

    mock_resolve.return_value = ModelCatalogEntry(
        id="reasoner",
        label="Reasoner",
        model_type="deepseek",
        model_name="deepseek-reasoner",
        temperature=0.6,
        base_url="https://example.com/v1",
    )
    mock_build.return_value = object()

    get_llm(model_id="reasoner")

    mock_build.assert_called_once()
    kwargs = mock_build.call_args.kwargs
    assert kwargs["model_type"] == "deepseek"
    assert kwargs["model_name"] == "deepseek-reasoner"
    assert kwargs["temperature"] == 0.6
    assert kwargs["model_base_url"] == "https://example.com/v1"
