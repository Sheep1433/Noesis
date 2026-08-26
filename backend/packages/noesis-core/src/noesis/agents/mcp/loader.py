"""从平台/用户 MCP 配置按 server 名加载工具。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

from noesis.agents.tools.mcp_invoke_wrapper import wrap_mcp_tools
from noesis.runtime.logging import logger
from noesis.config.mcp_config import get_profile_connections, resolve_server_connections

_TOOLS_CACHE: dict[tuple[frozenset[str], str], tuple[float, list[Any]]] = {}
_TOOLS_CACHE_TTL_SEC = 60.0


def _annotate_provider(tools: list[Any], server_id: str) -> list[Any]:
    for tool in tools:
        metadata = getattr(tool, "metadata", None)
        if not isinstance(metadata, dict):
            metadata = {}
            tool.metadata = metadata
        metadata["noesis_provider_key"] = f"mcp:{server_id}"
        version = metadata.get("server_version") or metadata.get("version")
        if version:
            metadata["noesis_provider_version"] = str(version)
    return tools


def clear_mcp_tools_cache() -> None:
    _TOOLS_CACHE.clear()


def format_mcp_error(exc: BaseException) -> str:
    """展开 ExceptionGroup / TaskGroup，避免只看到 unhandled errors in a TaskGroup。"""
    parts: list[str] = [f"{type(exc).__name__}: {exc}"]
    sub = getattr(exc, "exceptions", None)
    if isinstance(sub, (list, tuple)):
        for i, child in enumerate(sub[:5]):
            parts.append(f"  [{i}] {type(child).__name__}: {child}")
            nested = getattr(child, "exceptions", None)
            if isinstance(nested, (list, tuple)) and nested:
                parts.append(f"      → {type(nested[0]).__name__}: {nested[0]}")
    return " | ".join(parts) if len(parts) == 1 else "\n".join(parts)


def _format_mcp_error(exc: BaseException) -> str:
    return format_mcp_error(exc)


async def load_mcp_tools(
    profile: str,
    *,
    path: Path | None = None,
) -> list[Any]:
    connections = get_profile_connections(profile, path=path)
    tools: list[Any] = []
    for sid, cfg in connections.items():
        client = MultiServerMCPClient({sid: cfg})
        tools.extend(_annotate_provider(wrap_mcp_tools(await client.get_tools()), sid))
    logger.info(
        "MCP profile=%r 加载工具 %d 个（servers=%s）",
        profile,
        len(tools),
        list(connections),
    )
    return tools


async def load_mcp_tools_by_names(
    server_names: list[str],
    *,
    user_id: str | int | None = None,
    use_cache: bool = True,
) -> list[Any]:
    names = [str(n).strip() for n in server_names if str(n or "").strip()]
    if not names:
        return []

    cache_key = (frozenset(names), str(user_id or ""))
    now = time.monotonic()
    if use_cache:
        hit = _TOOLS_CACHE.get(cache_key)
        if hit and hit[0] > now:
            return list(hit[1])

    connections = resolve_server_connections(names, user_id=user_id)
    if not connections:
        return []

    tools: list[Any] = []
    for sid, cfg in connections.items():
        try:
            client = MultiServerMCPClient({sid: cfg})
            part = _annotate_provider(wrap_mcp_tools(await client.get_tools()), sid)
            tools.extend(part)
            logger.info("MCP server={!r} 加载工具 {} 个", sid, len(part))
        except Exception as e:
            logger.warning(
                "MCP server={!r} url={!r} 加载失败，已跳过:\n{}",
                sid,
                cfg.get("url"),
                _format_mcp_error(e),
            )

    if use_cache:
        _TOOLS_CACHE[cache_key] = (now + _TOOLS_CACHE_TTL_SEC, list(tools))
    return tools
