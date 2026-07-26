"""设置数据域的用户隔离、事务与敏感值安全回归。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from starlette.requests import Request

from noesis_server.common.security.secrets import (
    REDACTED,
    SecretCipher,
    SecretEncryptionUnavailable,
    redact_sensitive,
)
from noesis_server.exceptions.exception import ConflictException, NotFoundException
from noesis_server.exceptions.exception import AuthException
from noesis_server.infrastructure.database.repositories.settings import SettingsRepository
from noesis_server.services.settings_service import SettingsService
from noesis_server.services.auth.sessions import SessionService
from noesis_server.services.user_service import UserService


def test_secret_cipher_fails_closed_and_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SETTINGS_ENCRYPTION_KEY", raising=False)
    with pytest.raises(SecretEncryptionUnavailable):
        SecretCipher()

    cipher = SecretCipher(Fernet.generate_key())
    ciphertext = cipher.encrypt("sk-never-store-in-plaintext")
    assert "sk-never-store-in-plaintext" not in ciphertext
    assert cipher.decrypt(ciphertext) == "sk-never-store-in-plaintext"


def test_recursive_redaction_is_safe_for_api_log_audit_diagnostics_and_export() -> None:
    raw = {
        "apiKey": "api-value",
        "headers": {"Authorization": "Bearer token-value", "Accept": "application/json"},
        "nested": [{"password": "password-value"}, {"private_key": "private-value"}],
    }
    safe = redact_sensitive(raw)
    rendered = repr(safe)
    for leaked in ("api-value", "token-value", "password-value", "private-value"):
        assert leaked not in rendered
    assert safe["apiKey"] == REDACTED
    assert safe["headers"]["Accept"] == "application/json"


@pytest.mark.asyncio
async def test_provider_lookup_is_scoped_by_user() -> None:
    result = SimpleNamespace(scalar_one_or_none=lambda: None)
    db = SimpleNamespace(execute=AsyncMock(return_value=result))

    assert await SettingsRepository(db).get_provider(42, "provider-owned-by-another-user") is None
    statement = db.execute.await_args.args[0]
    assert "user_provider_connections.user_id" in str(statement)


@pytest.mark.asyncio
async def test_provider_update_returns_404_for_cross_user_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    monkeypatch.setattr(SettingsRepository, "get_provider", AsyncMock(return_value=None))

    with pytest.raises(NotFoundException):
        await SettingsService.update_provider_with_audit(
            db,
            user_id=2,
            provider_id="owned-by-user-1",
            expected_version=1,
            values={"display_name": "x"},
            summary={},
        )
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_provider_update_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    monkeypatch.setattr(SettingsRepository, "get_provider", AsyncMock(return_value=object()))
    monkeypatch.setattr(SettingsRepository, "update_provider", AsyncMock(return_value=False))

    with pytest.raises(ConflictException):
        await SettingsService.update_provider_with_audit(
            db,
            user_id=1,
            provider_id="p1",
            expected_version=3,
            values={"display_name": "new"},
            summary={},
        )
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_setting_update(monkeypatch: pytest.MonkeyPatch) -> None:
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    monkeypatch.setattr(SettingsRepository, "get_provider", AsyncMock(return_value=object()))
    monkeypatch.setattr(SettingsRepository, "update_provider", AsyncMock(return_value=True))
    monkeypatch.setattr(SettingsService, "append_audit", AsyncMock(side_effect=RuntimeError("audit unavailable")))

    with pytest.raises(RuntimeError, match="audit unavailable"):
        await SettingsService.update_provider_with_audit(
            db,
            user_id=1,
            provider_id="p1",
            expected_version=1,
            values={"display_name": "new"},
            summary={"api_key": "must-not-leak"},
        )
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_append_only_audit_redacts_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = []

    async def capture(_self, row):
        captured.append(row)

    monkeypatch.setattr(SettingsRepository, "append_audit", capture)
    await SettingsService.append_audit(
        SimpleNamespace(),
        user_id=1,
        action="provider.update",
        setting_domain="provider",
        summary={"api_key": "old-or-new-secret", "changed": ["credential"]},
    )
    assert captured[0].summary["api_key"] == REDACTED
    assert "old-or-new-secret" not in repr(captured[0].summary)


@pytest.mark.asyncio
async def test_settings_auth_rejects_bearer_without_cookie_session(monkeypatch: pytest.MonkeyPatch) -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/user/settings/audit",
            "headers": [(b"authorization", b"Bearer legacy-jwt")],
        }
    )
    monkeypatch.setattr(SessionService, "get_valid", AsyncMock(return_value=None))

    with pytest.raises(AuthException):
        await UserService.get_current_user(request, SimpleNamespace())
