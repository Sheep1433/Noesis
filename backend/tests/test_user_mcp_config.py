"""用户 MCP 配置合并与校验。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.mcp_config import (
    MCP_PROFILE_FAULT_OPERATION,
    McpJsonConfig,
    clear_mcp_config_cache,
    get_profile_server_names,
    list_merged_servers,
    load_user_mcp_json,
    resolve_server_connections,
    save_user_mcp_json,
    validate_user_server_config,
)
from config.user_data_paths import get_user_mcp_path


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_mcp_config_cache()
    yield
    clear_mcp_config_cache()


def test_validate_user_rejects_stdio() -> None:
    with pytest.raises(ValueError, match="stdio|transport"):
        validate_user_server_config({"transport": "stdio", "command": "npx"})


def test_validate_user_requires_http_url() -> None:
    with pytest.raises(ValueError, match="http"):
        validate_user_server_config(
            {"transport": "streamable_http", "url": "ftp://x"}
        )


def test_user_overrides_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config.user_data_paths as user_paths

    monkeypatch.setattr(user_paths, "_USERS_ROOT", tmp_path / "users")
    monkeypatch.setenv("MCP_CONFIG_PATH", str(tmp_path / "platform.json"))
    (tmp_path / "platform.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fault_ops": {
                        "transport": "streamable_http",
                        "url": "http://platform/mcp",
                    }
                },
                "profiles": {"fault_operation": ["fault_ops"]},
            }
        ),
        encoding="utf-8",
    )
    clear_mcp_config_cache()

    uid = "u_mcp_1"
    save_user_mcp_json(
        uid,
        McpJsonConfig(
            mcpServers={
                "fault_ops": {
                    "transport": "streamable_http",
                    "url": "http://user/mcp",
                },
                "custom": {
                    "transport": "sse",
                    "url": "https://custom/sse",
                },
            }
        ),
    )

    merged = list_merged_servers(uid)
    by_id = {s.id: s for s in merged}
    assert by_id["fault_ops"].source == "user"
    assert by_id["custom"].source == "user"

    conns = resolve_server_connections(["fault_ops", "custom"], user_id=uid)
    assert conns["fault_ops"]["url"] == "http://user/mcp"
    assert "custom" in conns
    assert get_user_mcp_path(uid).is_file()
    assert "custom" in load_user_mcp_json(uid).mcpServers


def test_profile_server_names_default() -> None:
    names = get_profile_server_names(MCP_PROFILE_FAULT_OPERATION)
    assert isinstance(names, list)
    assert len(names) >= 1
