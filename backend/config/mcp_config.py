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

# 未设置环境变量时的默认值（避免 URL 留成字面量 ${VAR}）
_ENV_DEFAULTS: dict[str, str] = {
    "NOESIS_MCP_REMOTE_URL": "http://localhost:8000/mcp",
}

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
    def _repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key in os.environ:
            return os.environ[key]
        if key in _ENV_DEFAULTS:
            return _ENV_DEFAULTS[key]
        # 可选密钥未配置时置空，避免把 ${VAR} 原样塞进 header
        if key.endswith(("_API_KEY", "_TOKEN", "_SECRET")):
            return ""
        return m.group(0)

    return _BRACED_VAR_RE.sub(_repl, value)


def _expand_deep(value: Any) -> Any:
    if isinstance(value, str):
        return _expand_env_vars(value)
    if isinstance(value, dict):
        return {k: _expand_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_deep(item) for item in value]
    return value


# 传给 langchain-mcp-adapters 的连接字段（不含 UI 元数据如 display_name）
_HTTP_CONNECTION_KEYS = frozenset(
    {
        "transport",
        "url",
        "headers",
        "timeout",
        "sse_read_timeout",
        "terminate_on_close",
        "session_kwargs",
        "httpx_client_factory",
        "auth",
    }
)
_STDIO_CONNECTION_KEYS = frozenset(
    {
        "transport",
        "command",
        "args",
        "env",
        "cwd",
        "encoding",
        "encoding_error_handler",
        "session_kwargs",
    }
)
_WS_CONNECTION_KEYS = frozenset({"transport", "url", "session_kwargs"})


def to_adapter_connection(server_cfg: dict[str, Any]) -> dict[str, Any]:
    """去掉 display_name 等元数据，只保留 MultiServerMCPClient 认识的字段。"""
    expanded = _expand_deep(server_cfg)
    if not isinstance(expanded, dict):
        raise TypeError("server 配置必须是对象")
    transport = str(expanded.get("transport") or "").strip()
    if transport in {"streamable_http", "streamable-http", "http", "sse"}:
        allowed = _HTTP_CONNECTION_KEYS
    elif transport == "stdio":
        allowed = _STDIO_CONNECTION_KEYS
    elif transport == "websocket":
        allowed = _WS_CONNECTION_KEYS
    else:
        # 未知 transport：至少丢掉明确的 UI 字段，避免 **kwargs 炸
        allowed = _HTTP_CONNECTION_KEYS | _STDIO_CONNECTION_KEYS | _WS_CONNECTION_KEYS
    return {k: v for k, v in expanded.items() if k in allowed}

def _read_mcp_json_file(cfg_path: Path) -> McpJsonConfig:
    if not cfg_path.is_file():
        raise FileNotFoundError(f"MCP 配置文件不存在: {cfg_path}")
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    return McpJsonConfig.model_validate(raw)


@lru_cache(maxsize=1)
def load_mcp_json(path: Path | None = None) -> McpJsonConfig:
    cfg_path = path or resolve_mcp_config_path()
    if not cfg_path.is_file():
        logger.warning("平台 MCP 配置不存在 path={}，使用空配置", cfg_path)
        return McpJsonConfig()
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
    _reject_user_placeholders(server_cfg)
    return dict(server_cfg)


def _reject_user_placeholders(value: Any, *, path: str = "") -> None:
    """个人 mcp.json 要求字面量；${ENV} 仅允许出现在平台 extensions/mcp/mcp.json。"""
    if isinstance(value, str):
        if "${" in value:
            where = path or "配置"
            raise ValueError(
                f"用户 MCP {where} 请直接填写字面量，不支持 ${'{ENV}'} 占位（收到 {value!r}）"
            )
        return
    if isinstance(value, dict):
        for k, v in value.items():
            child = f"{path}.{k}" if path else str(k)
            _reject_user_placeholders(v, path=child)
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _reject_user_placeholders(item, path=f"{path}[{i}]")


def expand_mcp_values(value: Any) -> Any:
    """展开 ${ENV}（平台配置 / 迁移旧用户文件用）。"""
    return _expand_deep(value)


def materialize_user_mcp_literals(user_id: str | int) -> bool:
    """
    若用户 mcp.json 仍含历史 ${ENV}，展开为字面量并写回。
    空字符串的 header 值会删掉（例如未配置的 API Key）。
    """
    cfg = load_user_mcp_json(user_id)
    if not cfg.mcpServers:
        return False

    def _has_placeholder(v: Any) -> bool:
        if isinstance(v, str):
            return "${" in v
        if isinstance(v, dict):
            return any(_has_placeholder(x) for x in v.values())
        if isinstance(v, list):
            return any(_has_placeholder(x) for x in v)
        return False

    if not any(_has_placeholder(raw) for raw in cfg.mcpServers.values()):
        return False

    new_servers: dict[str, Any] = {}
    for sid, raw in cfg.mcpServers.items():
        expanded = expand_mcp_values(raw)
        if not isinstance(expanded, dict):
            continue
        headers = expanded.get("headers")
        if isinstance(headers, dict):
            cleaned = {k: v for k, v in headers.items() if str(v).strip()}
            if cleaned:
                expanded["headers"] = cleaned
            else:
                expanded.pop("headers", None)
        new_servers[str(sid)] = expanded

    save_user_mcp_json(user_id, McpJsonConfig(mcpServers=new_servers))
    logger.info("已将用户 MCP 配置中的 ${{ENV}} 展开为字面量 user_id={}", user_id)
    return True


def _redact_url(url: str | None) -> str | None:
    if not url:
        return None
    if "?" in url:
        return url.split("?", 1)[0] + "?…"
    return url


def list_merged_servers(user_id: str | int | None = None) -> list[McpServerCatalogItem]:
    """平台 + 用户合并目录；同名用户覆盖平台。"""
    platform = load_mcp_json()
    user_cfg = load_user_mcp_json(user_id) if user_id is not None else McpJsonConfig()
    items: dict[str, McpServerCatalogItem] = {}
    for sid, raw in platform.mcpServers.items():
        items[sid] = McpServerCatalogItem(
            id=sid,
            source="platform",
            transport=str(raw.get("transport") or ""),
            url=_redact_url(_expand_env_vars(str(raw.get("url") or "")) or None),
            display_name=str(raw.get("display_name") or sid),
        )
    for sid, raw in user_cfg.mcpServers.items():
        items[sid] = McpServerCatalogItem(
            id=sid,
            source="user",
            transport=str(raw.get("transport") or ""),
            url=_redact_url(_expand_env_vars(str(raw.get("url") or "")) or None),
            display_name=str(raw.get("display_name") or sid),
        )
    return sorted(items.values(), key=lambda x: (x.source != "user", x.id.lower()))


def list_user_servers(user_id: str | int) -> list[McpServerCatalogItem]:
    """仅用户 mcp.json 中的 server（与配置编辑器内容一致）。"""
    user_cfg = load_user_mcp_json(user_id)
    items: list[McpServerCatalogItem] = []
    for sid, raw in user_cfg.mcpServers.items():
        items.append(
            McpServerCatalogItem(
                id=sid,
                source="user",
                transport=str(raw.get("transport") or ""),
                url=_redact_url(_expand_env_vars(str(raw.get("url") or "")) or None),
                display_name=str(raw.get("display_name") or sid),
            )
        )
    return sorted(items, key=lambda x: x.id.lower())


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
        connections[sid] = to_adapter_connection(server_cfg)
    return connections


def clear_mcp_config_cache() -> None:
    load_mcp_json.cache_clear()
