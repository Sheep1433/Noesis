"""Notification policy, diagnostics isolation, and secret-free transfer."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from noesis.repositories.settings_repository import SettingsRepository
from noesis.services.notification_preference_service import NotificationPreferenceService
from noesis.services.settings_diagnostics_service import SettingsDiagnosticsService
from noesis.services.settings_transfer_service import SettingsTransferService, _strip_sensitive


@pytest.mark.asyncio
async def test_disabled_notification_only_suppresses_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    row = SimpleNamespace(enabled=False)
    monkeypatch.setattr(SettingsRepository, "get_notification_preference", AsyncMock(return_value=row))
    assert await NotificationPreferenceService.should_notify(SimpleNamespace(), 1, "automation.succeeded", "web") is False


@pytest.mark.asyncio
async def test_diagnostic_failure_and_timeout_are_isolated() -> None:
    async def failed():
        raise RuntimeError("postgresql://user:password@internal-host")

    async def slow():
        await asyncio.sleep(0.02)
        return "healthy", "ok", None

    failed_result, timeout_result = await asyncio.gather(
        SettingsDiagnosticsService._run("database", failed, timeout=0.1),
        SettingsDiagnosticsService._run("qdrant", slow, timeout=0.001),
    )
    assert failed_result["status"] == "unavailable"
    assert timeout_result["status"] == "unavailable"
    rendered = repr([failed_result, timeout_result])
    assert "password" not in rendered
    assert "internal-host" not in rendered


def test_transfer_recursively_excludes_secrets_and_sensitive_headers() -> None:
    raw = {
        "providers": [{"display_name": "P", "api_key": "sk-leak"}],
        "channels": [{"bot_token": "token-leak"}],
        "mcp": {"server": {"headers": {"Authorization": "Bearer leak"}}},
        "memory": {"USER.md": "safe"},
    }
    safe = _strip_sensitive(raw)
    rendered = repr(safe)
    assert "sk-leak" not in rendered
    assert "token-leak" not in rendered
    assert "Bearer leak" not in rendered
    assert safe["memory"]["USER.md"] == "safe"


@pytest.mark.asyncio
async def test_export_covers_domains_without_reversible_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    task = SimpleNamespace(name="T", cron_expr="0 9 * * *", timezone="Asia/Shanghai", enabled=True, qa_type="SUPER_AGENT_QA", prompt="hello", session_binding="none", delivery="none")
    preference = SimpleNamespace(event_type="automation.failed", delivery_surface="web", enabled=True)

    class Result:
        def __init__(self, rows): self.rows = rows
        def scalars(self): return self.rows

    async def execute(statement):
        sql = str(statement)
        if "user_scheduled_tasks" in sql:
            return Result([task])
        return Result([preference])

    db = SimpleNamespace(execute=execute)
    monkeypatch.setattr("noesis.services.settings_transfer_service.MessagingChannelService.list_channels", lambda _uid: [{"type": "telegram", "display_name": "Bot", "enabled": True, "bot_token": "leak"}])
    monkeypatch.setattr("noesis.services.settings_transfer_service.UserMemoryService.read_file", lambda _uid, name: {"content": f"safe {name}"})
    monkeypatch.setattr("noesis.services.settings_transfer_service.load_user_mcp_json", lambda _uid: SimpleNamespace(mcpServers={"s": {"url": "https://example.com", "headers": {"Authorization": "Bearer leak"}}}))

    exported = await SettingsTransferService.export(db, 1)
    rendered = json.dumps(exported, ensure_ascii=False)

    assert set(exported["domains"]) == {"automation", "channels", "memory", "notifications", "mcp"}
    for forbidden in ("Bearer leak", "bot_token"):
        assert forbidden not in rendered


def test_import_rejects_secret_fields_and_unsupported_version() -> None:
    with pytest.raises(ValueError, match="敏感字段"):
        SettingsTransferService._validate({"schema_version": 1, "domains": {"providers": [{"api_key": "leak"}]}})
    with pytest.raises(ValueError, match="版本"):
        SettingsTransferService._validate({"schema_version": 99, "domains": {}})
    with pytest.raises(ValueError, match="不支持的设置域"):
        SettingsTransferService._validate({"schema_version": 1, "domains": {"providers": []}})


@pytest.mark.asyncio
async def test_import_rolls_back_failed_memory_domain_and_audits_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"USER.md": "old user", "AGENTS.md": "old agents"}
    failed = False

    def read_file(_uid, name):
        return {"content": state[name]}

    def write_file(_uid, name, content):
        nonlocal failed
        if name == "AGENTS.md" and content == "new agents" and not failed:
            failed = True
            raise OSError("write failed")
        state[name] = content
        return {"content": content}

    monkeypatch.setattr(SettingsTransferService, "preview", AsyncMock(return_value={"preview_id": "p1", "errors": []}))
    monkeypatch.setattr("noesis.services.settings_transfer_service.UserMemoryService.read_file", read_file)
    monkeypatch.setattr("noesis.services.settings_transfer_service.UserMemoryService.write_file", write_file)
    audit = AsyncMock()
    monkeypatch.setattr("noesis.services.settings_transfer_service.SettingsService.append_audit", audit)
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    manifest = {"schema_version": 1, "domains": {"memory": {"USER.md": "new user", "AGENTS.md": "new agents"}}}

    with pytest.raises(OSError):
        await SettingsTransferService.apply(db, 1, manifest, "p1")

    assert state == {"USER.md": "old user", "AGENTS.md": "old agents"}
    assert audit.await_args.kwargs["action"] == "settings.import.failed"
