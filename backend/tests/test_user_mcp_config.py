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
    assert names == ["remote_ops"]


def test_validate_user_rejects_env_placeholder() -> None:
    with pytest.raises(ValueError, match="字面量|占位"):
        validate_user_server_config(
            {
                "transport": "streamable_http",
                "url": "http://${HOST}/mcp",
            }
        )


def test_materialize_user_mcp_literals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config.user_data_paths as user_paths
    from config.mcp_config import materialize_user_mcp_literals

    monkeypatch.setattr(user_paths, "_USERS_ROOT", tmp_path / "users")
    monkeypatch.setenv("NOESIS_MCP_REMOTE_URL", "http://127.0.0.1:8000/mcp")
    uid = "u_mat"
    path = get_user_mcp_path(uid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "remote_ops": {
                        "transport": "streamable_http",
                        "url": "${NOESIS_MCP_REMOTE_URL}",
                        "headers": {"CONTEXT7_API_KEY": ""},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    assert materialize_user_mcp_literals(uid) is True
    cfg = load_user_mcp_json(uid)
    assert cfg.mcpServers["remote_ops"]["url"] == "http://127.0.0.1:8000/mcp"
    assert "headers" not in cfg.mcpServers["remote_ops"]


def test_ensure_user_config_seeded_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config.user_data_paths as user_paths
    from services.mcp_service import McpService

    monkeypatch.setattr(user_paths, "_USERS_ROOT", tmp_path / "users")
    uid = "u_seed"
    path = get_user_mcp_path(uid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"mcpServers": {}}\n', encoding="utf-8")

    out = McpService.get_user_config_file(uid)
    assert "context7" in out.content
    assert "remote_ops" in out.content
    assert "${" not in out.content
    assert "http://localhost:8000/mcp" in out.content
    cfg = load_user_mcp_json(uid)
    assert set(cfg.mcpServers) == {"context7", "remote_ops"}


def test_save_user_config_file_rejects_stdio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config.user_data_paths as user_paths
    from services.mcp_service import McpService

    monkeypatch.setattr(user_paths, "_USERS_ROOT", tmp_path / "users")
    with pytest.raises(ValueError, match="stdio|transport"):
        McpService.save_user_config_file(
            "u1",
            json.dumps(
                {
                    "mcpServers": {
                        "bad": {"transport": "stdio", "command": "npx", "args": []}
                    }
                }
            ),
        )


def test_save_user_config_file_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config.user_data_paths as user_paths
    from services.mcp_service import McpService

    monkeypatch.setattr(user_paths, "_USERS_ROOT", tmp_path / "users")
    out = McpService.save_user_config_file(
        "u2",
        json.dumps(
            {
                "mcpServers": {
                    "ctx": {
                        "transport": "streamable_http",
                        "url": "https://example.com/mcp",
                    }
                }
            }
        ),
    )
    assert out.exists
    assert "ctx" in out.content
    assert get_user_mcp_path("u2").is_file()
