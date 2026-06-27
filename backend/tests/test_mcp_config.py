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
    assert "fault_ops" in cfg.mcpServers
    assert MCP_PROFILE_FAULT_OPERATION in cfg.profiles


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


def test_unknown_profile_raises(tmp_path: Path) -> None:
    cfg_path = tmp_path / "mcp.json"
    _write_mcp_json(cfg_path)

    with pytest.raises(KeyError, match="unknown_profile"):
        get_profile_connections("unknown_profile", path=cfg_path)
