"""MCP 客户端配置：从 mcp.json 加载 MultiServerMCPClient 连接。"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from common.logging import logger
from config.extensions_paths import extensions_root, repo_root

_BRACED_VAR_RE = re.compile(r"\$\{([^}]+)\}")

# Agent 使用的 profile 名称（与 mcp.json profiles 键对齐）
MCP_PROFILE_FAULT_OPERATION = "fault_operation"
MCP_PROFILE_SIMPLE_MCP = "simple_mcp"


class McpJsonConfig(BaseModel):
    """mcp.json 顶层结构。"""

    mcpServers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    profiles: dict[str, list[str]] = Field(default_factory=dict)


def resolve_mcp_config_path() -> Path:
    """解析 mcp.json 路径：MCP_CONFIG_PATH > config other.mcp_config_path > 默认 extensions/mcp/mcp.json。"""
    raw = (os.environ.get("MCP_CONFIG_PATH") or "").strip()
    if not raw:
        from config.env import OtherConfig

        raw = (OtherConfig.mcp_config_path or "").strip()
    if raw:
        p = Path(raw)
        return p.resolve() if p.is_absolute() else (repo_root() / raw).resolve()
    return (extensions_root() / "mcp" / "mcp.json").resolve()


def _expand_env_vars(value: str) -> str:
    return _BRACED_VAR_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)


def _expand_deep(value: Any) -> Any:
    if isinstance(value, str):
        return _expand_env_vars(value)
    if isinstance(value, dict):
        return {k: _expand_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_deep(item) for item in value]
    return value


@lru_cache(maxsize=1)
def load_mcp_json(path: Path | None = None) -> McpJsonConfig:
    """加载并缓存 mcp.json。"""
    cfg_path = path or resolve_mcp_config_path()
    if not cfg_path.is_file():
        raise FileNotFoundError(f"MCP 配置文件不存在: {cfg_path}")
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg = McpJsonConfig.model_validate(raw)
    logger.debug(
        "已加载 MCP 配置 path={} servers={} profiles={}",
        cfg_path,
        list(cfg.mcpServers),
        list(cfg.profiles),
    )
    return cfg


def get_profile_connections(
    profile: str,
    *,
    path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """按 profile 取出 MultiServerMCPClient 所需的 connections 字典。"""
    cfg = load_mcp_json(path)
    server_names = cfg.profiles.get(profile)
    if not server_names:
        raise KeyError(
            f"MCP profile {profile!r} 未在 mcp.json profiles 中定义；"
            f"可用: {sorted(cfg.profiles)}"
        )

    connections: dict[str, dict[str, Any]] = {}
    for name in server_names:
        server_cfg = cfg.mcpServers.get(name)
        if not server_cfg:
            raise KeyError(
                f"MCP server {name!r} 未在 mcp.json mcpServers 中定义；"
                f"可用: {sorted(cfg.mcpServers)}"
            )
        transport = server_cfg.get("transport")
        if not transport:
            raise ValueError(f"MCP server {name!r} 缺少 transport 字段")
        connections[name] = _expand_deep(server_cfg)
    return connections


def clear_mcp_config_cache() -> None:
    """测试或热重载时清除缓存。"""
    load_mcp_json.cache_clear()
