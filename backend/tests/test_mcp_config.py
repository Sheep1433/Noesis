"""MCP 配置加载测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.mcp_config import (
    MCP_PROFILE_FAULT_OPERATION,
    clear_mcp_config_cache,
    get_profile_connections,
    load_mcp_json,
)


@pytest.fixture(autouse=True)
def _clear_mcp_cache() -> None:
    clear_mcp_config_cache()
    yield
    clear_mcp_config_cache()


def _write_mcp_json(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fault_ops": {
                        "transport": "streamable_http",
                        "url": "http://mcp.example:9000/mcp",
                    },
                    "ssh": {
                        "transport": "streamable_http",
                        "url": "http://localhost:8000/mcp",
                    },
                },
                "profiles": {
                    "fault_operation": ["fault_ops"],
                    "simple_mcp": ["ssh"],
                },
            }
        ),
        encoding="utf-8",
    )


def test_load_default_mcp_json() -> None:
    cfg = load_mcp_json()
    assert "context7" in cfg.mcpServers
    assert "remote_ops" in cfg.mcpServers
    assert MCP_PROFILE_FAULT_OPERATION in cfg.profiles
    assert "remote_ops" in cfg.profiles[MCP_PROFILE_FAULT_OPERATION]

def test_get_profile_connections(tmp_path: Path) -> None:
    cfg_path = tmp_path / "mcp.json"
    _write_mcp_json(cfg_path)

    connections = get_profile_connections(
        MCP_PROFILE_FAULT_OPERATION,
        path=cfg_path,
    )

    assert list(connections) == ["fault_ops"]
    assert connections["fault_ops"]["url"] == "http://mcp.example:9000/mcp"
    assert connections["fault_ops"]["transport"] == "streamable_http"


def test_get_profile_connections_expands_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_HOST", "env-host")
    cfg_path = tmp_path / "mcp.json"
    cfg_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fault_ops": {
                        "transport": "streamable_http",
                        "url": "http://${MCP_HOST}:8000/mcp",
                    }
                },
                "profiles": {"fault_operation": ["fault_ops"]},
            }
        ),
        encoding="utf-8",
    )

    connections = get_profile_connections(MCP_PROFILE_FAULT_OPERATION, path=cfg_path)
    assert connections["fault_ops"]["url"] == "http://env-host:8000/mcp"


def test_expand_remote_url_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOESIS_MCP_REMOTE_URL", raising=False)
    cfg_path = tmp_path / "mcp.json"
    cfg_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "remote_ops": {
                        "transport": "streamable_http",
                        "url": "${NOESIS_MCP_REMOTE_URL}",
                    }
                },
                "profiles": {"fault_operation": ["remote_ops"]},
            }
        ),
        encoding="utf-8",
    )
    connections = get_profile_connections(MCP_PROFILE_FAULT_OPERATION, path=cfg_path)
    assert connections["remote_ops"]["url"] == "http://localhost:8000/mcp"


def test_to_adapter_connection_strips_display_name() -> None:
    from config.mcp_config import to_adapter_connection

    conn = to_adapter_connection(
        {
            "transport": "streamable_http",
            "url": "https://mcp.example/mcp",
            "display_name": "Example",
            "headers": {"X": "1"},
        }
    )
    assert "display_name" not in conn
    assert conn["transport"] == "streamable_http"
    assert conn["url"] == "https://mcp.example/mcp"
    assert conn["headers"] == {"X": "1"}


def test_unknown_profile_raises(tmp_path: Path) -> None:
    cfg_path = tmp_path / "mcp.json"
    _write_mcp_json(cfg_path)

    with pytest.raises(KeyError, match="unknown_profile"):
        get_profile_connections("unknown_profile", path=cfg_path)
