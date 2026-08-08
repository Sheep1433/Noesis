"""设置控制面共享契约与 capability flags。"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from noesis_server.schemas.settings_vo import (
    SecretWriteAction,
    SecretWriteCommand,
    SettingsCapabilities,
)
from noesis.services.settings_service import SettingsService


def test_secret_write_command_enforces_three_state_contract() -> None:
    assert SecretWriteCommand(action="keep").value is None
    assert SecretWriteCommand(action="clear").value is None
    replace = SecretWriteCommand(action="replace", value="new-secret")
    assert replace.action is SecretWriteAction.REPLACE

    with pytest.raises(ValidationError, match="replace 必须提供"):
        SecretWriteCommand(action="replace")
    with pytest.raises(ValidationError, match="keep/clear 不得携带"):
        SecretWriteCommand(action="keep", value="must-not-leak")


def test_settings_capabilities_come_from_runtime_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flags = SettingsCapabilities(
        provider_models=True,
        mcp_management=False,
        automation_operations=True,
        channel_operations=False,
        agent_context=True,
        observability=False,
        import_export=True,
    )
    config = SimpleNamespace(settings_features=flags)
    monkeypatch.setattr(
        "noesis.services.settings_service.load_app_yaml",
        lambda: config,
    )

    result = SettingsService.get_capabilities()

    assert result == flags
    assert result.provider_models is True
    assert result.mcp_management is False
