"""MCP 客户端配置：平台 mcp.json + 用户 mcp.json 合并解析。"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from common.logging import logger
from config.extensions_paths import extensions_root, repo_root
from config.user_data_paths import ensure_user_mcp_path, get_user_mcp_path

_BRACED_VAR_RE = re.compile(r"\$\{([^}]+)\}")

MCP_PROFILE_FAULT_OPERATION = "fault_operation"
MCP_PROFILE_SIMPLE_MCP = "simple_mcp"

USER_ALLOWED_TRANSPORTS = frozenset({"streamable_http", "sse"})
McpServerSource = Literal["platform", "user"]
_SERVER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class McpJsonConfig(BaseModel):
    mcpServers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    profiles: dict[str, list[str]] = Field(default_factory=dict)


class McpServerCatalogItem(BaseModel):
    id: str
    source: McpServerSource
    transport: str
    url: str | None = None
    display_name: str | None = None


def resolve_mcp_config_path() -> Path:
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


def _read_mcp_json_file(cfg_path: Path) -> McpJsonConfig:
    if not cfg_path.is_file():
        raise FileNotFoundError(f"MCP 配置文件不存在: {cfg_path}")
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    return McpJsonConfig.model_validate(raw)


@lru_cache(maxsize=1)
def load_mcp_json(path: Path | None = None) -> McpJsonConfig:
    cfg_path = path or resolve_mcp_config_path()
    cfg = _read_mcp_json_file(cfg_path)
    logger.debug(
        "已加载平台 MCP 配置 path={} servers={} profiles={}",
        cfg_path,
        list(cfg.mcpServers),
        list(cfg.profiles),
    )
    return cfg


def load_user_mcp_json(user_id: str | int) -> McpJsonConfig:
    path = get_user_mcp_path(user_id)
    if not path.is_file():
        return McpJsonConfig()
    try:
        return _read_mcp_json_file(path)
    except Exception as e:
        logger.warning("读取用户 MCP 配置失败 user_id={} err={}", user_id, e)
        return McpJsonConfig()


def save_user_mcp_json(user_id: str | int, cfg: McpJsonConfig) -> Path:
    path = ensure_user_mcp_path(user_id)
    payload = {"mcpServers": cfg.mcpServers}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def validate_server_id(server_id: str) -> str:
    sid = (server_id or "").strip()
    if not _SERVER_ID_RE.match(sid):
        raise ValueError(f"非法 MCP server id: {server_id!r}")
    return sid


def validate_user_server_config(server_cfg: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(server_cfg, dict):
        raise ValueError("server 配置必须为对象")
    transport = str(server_cfg.get("transport") or "").strip()
    if transport not in USER_ALLOWED_TRANSPORTS:
        raise ValueError(
            f"用户 MCP 仅支持 transport={sorted(USER_ALLOWED_TRANSPORTS)}，收到 {transport!r}"
        )
    if "command" in server_cfg or transport == "stdio":
        raise ValueError("用户 MCP 禁止 stdio/command")
    url = str(server_cfg.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("用户 MCP url 须为 http:// 或 https://")
    return dict(server_cfg)


def _redact_url(url: str | None) -> str | None:
    if not url:
        return None
    if "?" in url:
        return url.split("?", 1)[0] + "?…"
    return url


def list_merged_servers(user_id: str | int | None = None) -> list[McpServerCatalogItem]:
    platform = load_mcp_json()
    user_cfg = load_user_mcp_json(user_id) if user_id is not None else McpJsonConfig()
    items: dict[str, McpServerCatalogItem] = {}
    for sid, raw in platform.mcpServers.items():
        items[sid] = McpServerCatalogItem(
            id=sid,
            source="platform",
            transport=str(raw.get("transport") or ""),
            url=_redact_url(str(raw.get("url") or "") or None),
            display_name=str(raw.get("display_name") or sid),
        )
    for sid, raw in user_cfg.mcpServers.items():
        items[sid] = McpServerCatalogItem(
            id=sid,
            source="user",
            transport=str(raw.get("transport") or ""),
            url=_redact_url(str(raw.get("url") or "") or None),
            display_name=str(raw.get("display_name") or sid),
        )
    return sorted(items.values(), key=lambda x: (x.source != "user", x.id.lower()))


def get_merged_server_map(user_id: str | int | None = None) -> dict[str, dict[str, Any]]:
    platform = load_mcp_json()
    merged = dict(platform.mcpServers)
    if user_id is not None:
        merged.update(load_user_mcp_json(user_id).mcpServers)
    return merged


def get_profile_server_names(profile: str, *, path: Path | None = None) -> list[str]:
    cfg = load_mcp_json(path)
    names = cfg.profiles.get(profile)
    if not names:
        raise KeyError(
            f"MCP profile {profile!r} 未在 mcp.json profiles 中定义；"
            f"可用: {sorted(cfg.profiles)}"
        )
    return list(names)


def get_profile_connections(
    profile: str,
    *,
    path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    names = get_profile_server_names(profile, path=path)
    return resolve_server_connections(names, user_id=None, platform_path=path)


def resolve_server_connections(
    server_names: list[str],
    *,
    user_id: str | int | None = None,
    platform_path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    if platform_path is not None:
        server_map = dict(load_mcp_json(platform_path).mcpServers)
    else:
        server_map = get_merged_server_map(user_id)

    connections: dict[str, dict[str, Any]] = {}
    for name in server_names:
        sid = str(name or "").strip()
        if not sid:
            continue
        server_cfg = server_map.get(sid)
        if not server_cfg:
            logger.warning("MCP server {!r} 未找到，已跳过", sid)
            continue
        if not server_cfg.get("transport"):
            logger.warning("MCP server {!r} 缺少 transport，已跳过", sid)
            continue
        connections[sid] = _expand_deep(server_cfg)
    return connections


def clear_mcp_config_cache() -> None:
    load_mcp_json.cache_clear()
