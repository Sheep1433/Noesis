"""设置数据域的用户隔离、事务与敏感值安全回归。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from starlette.requests import Request

from noesis.security.secrets import (
    REDACTED,
    SecretCipher,
    SecretEncryptionUnavailable,
    redact_sensitive,
)
from noesis.errors.exceptions import AuthException
from noesis_server.infrastructure.database.repositories.settings import SettingsRepository
from noesis.services.settings_service import SettingsService
from noesis.services.auth.sessions import SessionService
from noesis.services.user_service import UserService


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
async def test_append_only_audit_redacts_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = []

    async def capture(_self, row):
        captured.append(row)

    monkeypatch.setattr(SettingsRepository, "append_audit", capture)
    await SettingsService.append_audit(
        SimpleNamespace(),
        user_id=1,
        action="channel.update",
        setting_domain="channel",
        summary={"bot_token": "old-or-new-secret", "changed": ["credential"]},
    )
    assert captured[0].summary["bot_token"] == REDACTED
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
