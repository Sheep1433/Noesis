"""从平台/用户 MCP 配置按 server 名加载工具。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

from agent.tools.mcp_invoke_wrapper import wrap_mcp_tools
from common.logging import logger
from config.mcp_config import get_profile_connections, resolve_server_connections

_TOOLS_CACHE: dict[tuple[frozenset[str], str], tuple[float, list[Any]]] = {}
_TOOLS_CACHE_TTL_SEC = 60.0


def clear_mcp_tools_cache() -> None:
    _TOOLS_CACHE.clear()


async def load_mcp_tools(
    profile: str,
    *,
    path: Path | None = None,
) -> list[Any]:
    connections = get_profile_connections(profile, path=path)
    client = MultiServerMCPClient(connections)
    tools = wrap_mcp_tools(await client.get_tools())
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
            part = wrap_mcp_tools(await client.get_tools())
            tools.extend(part)
            logger.info("MCP server={!r} 加载工具 {} 个", sid, len(part))
        except Exception as e:
            logger.warning("MCP server={!r} 加载失败，已跳过: {}", sid, e)

    if use_cache:
        _TOOLS_CACHE[cache_key] = (now + _TOOLS_CACHE_TTL_SEC, list(tools))
    return tools
