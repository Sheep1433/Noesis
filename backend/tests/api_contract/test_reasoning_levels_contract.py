"""推理档位（reasoning_levels）接口契约。

覆盖 GET /api/models 下拉行/预设字段透出，与 user_llm 模型 upsert 的
新字段及「省略=不改」兼容语义（旧客户端不受 extra=forbid 影响）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from noesis.services.user_llm_service import UserLLMService


def test_models_catalog_rows_expose_reasoning_levels(contract_client) -> None:
    with (
        patch("server.api.model_api.list_public_models") as list_models,
        patch("server.api.model_api.get_default_model_id", return_value="m1"),
        patch("server.api.model_api._platform_provider_info", return_value=None),
        patch("server.api.model_api.get_catalog_vision_meta", return_value={}),
        patch.object(UserLLMService, "list_models", AsyncMock(return_value=[])),
        patch.object(UserLLMService, "get_default_model", AsyncMock(return_value=None)),
    ):
        list_models.return_value = [
            {
                "id": "m1",
                "label": "M1",
                "provider": "p",
                "model_type": "openai",
                "is_default": True,
                "supports_vision": False,
                "context_window": 64000,
                "reasoning_levels": ["off", "low", "high"],
            }
        ]
        resp = contract_client.get("/api/models")

    assert resp.status_code == 200
    models = resp.json()["data"]["models"]
    assert models[0]["reasoning_levels"] == ["off", "low", "high"]


def test_models_presets_expose_reasoning_levels(contract_client) -> None:
    with (
        patch("server.api.model_api.list_public_models", return_value=[]),
        patch("server.api.model_api.get_default_model_id", return_value="m1"),
        patch("server.api.model_api._platform_provider_info", return_value=None),
        patch("server.api.model_api.get_catalog_vision_meta", return_value={}),
        patch.object(UserLLMService, "list_models", AsyncMock(return_value=[])),
        patch.object(UserLLMService, "get_default_model", AsyncMock(return_value=None)),
    ):
        resp = contract_client.get("/api/models")

    # env.py 构造的 presets 字典含 reasoning_levels 键（ProviderPresetItem 透出）
    presets = resp.json()["data"]["provider_presets"]
    assert isinstance(presets, list)
    for preset in presets:
        assert "reasoning_levels" in preset


def test_model_upsert_accepts_reasoning_levels(contract_client) -> None:
    """POST models 带 reasoning_levels：字段合法进入 service 层。"""
    created = {
        "entry_id": "e1",
        "provider_id": "p1",
        "model_id": "glm-5",
        "label": "GLM",
        "temperature": None,
        "context_window": 200000,
        "reasoning_levels": ["low", "high"],
    }
    with patch.object(
        UserLLMService, "create_model", AsyncMock(return_value=created)
    ) as create_mock:
        resp = contract_client.post(
            "/api/user/llm/models",
            json={
                "provider_id": "p1",
                "model_id": "glm-5",
                "label": "GLM",
                "context_window": 200000,
                "reasoning_levels": ["low", "high"],
            },
        )
    assert resp.status_code == 200
    create_mock.assert_awaited_once()
    assert create_mock.await_args.kwargs.get("reasoning_levels") == ["low", "high"]


def test_model_upsert_omitting_reasoning_levels_still_valid(contract_client) -> None:
    """旧客户端省略 reasoning_levels：extra=forbid 不报错（None=不改/未声明）。"""
    created = {
        "entry_id": "e1",
        "provider_id": "p1",
        "model_id": "glm-5",
        "label": "GLM",
        "temperature": None,
        "context_window": 200000,
    }
    with patch.object(
        UserLLMService, "create_model", AsyncMock(return_value=created)
    ) as create_mock:
        resp = contract_client.post(
            "/api/user/llm/models",
            json={"provider_id": "p1", "model_id": "glm-5", "label": "GLM"},
        )
    assert resp.status_code == 200
    assert create_mock.await_args.kwargs.get("reasoning_levels") is None
