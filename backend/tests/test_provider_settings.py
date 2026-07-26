"""Provider 设置闭环回归。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.fernet import Fernet

from noesis_server.exceptions.exception import ConflictException, NotFoundException
from noesis_server.infrastructure.database.repositories.settings import SettingsRepository
from noesis_server.schemas.settings_vo import ModelPurposeBindingWrite, ProviderCreate
from noesis_server.services.provider_service import ProviderService
from noesis_server.services.settings_service import SettingsService
from noesis.llm.runtime_snapshot import (
    RuntimeModelSnapshot,
    get_runtime_model_snapshot,
    set_runtime_model_snapshot,
)
from noesis_server.services.qa.helpers import _resolve_model_for_query


class _FakeClient:
    def __init__(self, response: httpx.Response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, headers):
        request = httpx.Request("GET", url, headers=headers)
        self.response.request = request
        return self.response


def _provider(**overrides):
    defaults = dict(
        id="p1", user_id=1, provider_type="openai", display_name="Primary",
        base_url="https://provider.example/v1", enabled=True,
        secret_ciphertext="ciphertext", secret_suffix="cret", secret_updated_at=123,
        version=1, created_at=100, updated_at=100,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_provider_read_model_never_exposes_ciphertext() -> None:
    payload = ProviderService._view(_provider(secret_ciphertext="raw-or-ciphertext-secret")).model_dump()
    assert payload["secret"] == {"configured": True, "suffix": "cret", "updated_at": "123"}
    assert "ciphertext" not in repr(payload)


@pytest.mark.asyncio
async def test_create_provider_encrypts_secret_and_audits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    captured = []

    async def add(_self, row):
        captured.append(row)

    monkeypatch.setattr(SettingsRepository, "add_provider", add)
    monkeypatch.setattr(SettingsService, "append_audit", AsyncMock())
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    view = await ProviderService.create(
        db, 1,
        ProviderCreate(provider_type="openai", display_name="Primary", base_url="https://provider.example/v1", secret={"action": "replace", "value": "sk-super-secret"}),
    )
    assert captured[0].secret_ciphertext != "sk-super-secret"
    assert "sk-super-secret" not in repr(view.model_dump())
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_provider_probe_classifies_auth_failure_without_leaking_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key()
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", key.decode())
    from noesis_server.common.security.secrets import SecretCipher
    row = _provider(secret_ciphertext=SecretCipher(key).encrypt("sk-never-leak"))
    monkeypatch.setattr(ProviderService, "get_row", AsyncMock(return_value=row))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: _FakeClient(httpx.Response(401, text="invalid sk-never-leak")))

    result = await ProviderService.probe(SimpleNamespace(), 1, "p1")
    assert result.ok is False
    assert result.error_category == "authentication"
    assert "sk-never-leak" not in repr(result.model_dump())


@pytest.mark.asyncio
async def test_provider_model_discovery_returns_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key()
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", key.decode())
    from noesis_server.common.security.secrets import SecretCipher
    row = _provider(secret_ciphertext=SecretCipher(key).encrypt("sk-ok"))
    monkeypatch.setattr(ProviderService, "get_row", AsyncMock(return_value=row))
    response = httpx.Response(200, json={"data": [{"id": "gpt-4o"}, {"id": "text-embedding-3-small"}]})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: _FakeClient(response))

    models = await ProviderService.discover_models(SimpleNamespace(), 1, "p1")
    assert models[0].capabilities == ["chat", "vision"]
    assert models[1].capabilities == ["embedding"]


@pytest.mark.asyncio
async def test_binding_rejects_cross_user_and_capability_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    body = ModelPurposeBindingWrite(provider_id="other-user-provider", model_id="text-only", model_name="Text", capabilities=["chat"])
    monkeypatch.setattr(SettingsRepository, "get_provider", AsyncMock(return_value=None))
    with pytest.raises(NotFoundException):
        await ProviderService.bind(SimpleNamespace(), 1, "embedding", body)

    monkeypatch.setattr(ProviderService, "get_row", AsyncMock(return_value=_provider()))
    monkeypatch.setattr(ProviderService, "_request_models", AsyncMock(return_value=[SimpleNamespace(id="text-only", name="Text", capabilities=["chat"])]))
    with pytest.raises(ConflictException) as exc_info:
        await ProviderService.bind(SimpleNamespace(), 1, "embedding", body)
    assert "不支持" in exc_info.value.message


@pytest.mark.asyncio
async def test_user_chat_binding_is_fixed_as_run_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = RuntimeModelSnapshot(
        id="user:p1:model-a", provider_id="p1", purpose="chat", model_type="openai",
        model_name="model-a", base_url="https://example.com/v1", api_key="secret",
    )
    monkeypatch.setattr(ProviderService, "resolve_runtime_snapshots", AsyncMock(return_value=[snapshot]))
    monkeypatch.setattr(
        "noesis_server.services.qa.helpers.ChatService.get_session_by_id",
        AsyncMock(return_value=None),
    )
    set_runtime_model_snapshot(None)

    resolved = await _resolve_model_for_query(
        session_id="session-1", user_id="1", request_model_id=None, db=SimpleNamespace(),
    )
    assert resolved == "user:p1:model-a"
    assert get_runtime_model_snapshot() is snapshot

    # 数据库绑定之后如何变化，不会修改已经捕获的不可变 run 快照。
    monkeypatch.setattr(ProviderService, "resolve_runtime_snapshots", AsyncMock(return_value=[]))
    assert get_runtime_model_snapshot().id == "user:p1:model-a"
    set_runtime_model_snapshot(None)
