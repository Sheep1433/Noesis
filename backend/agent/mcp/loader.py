"""从 mcp.json 加载 MCP 工具。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

from agent.tools.mcp_invoke_wrapper import wrap_mcp_tools
from common.logging import logger
from config.mcp_config import get_profile_connections


async def load_mcp_tools(
    profile: str,
    *,
    path: Path | None = None,
) -> list[Any]:
    """按 profile 连接 MCP 并返回包装后的 LangChain 工具列表。"""
    connections = get_profile_connections(profile, path=path)
    client = MultiServerMCPClient(connections)
    tools = wrap_mcp_tools(await client.get_tools())
    logger.info("MCP profile=%r 加载工具 %d 个（servers=%s）", profile, len(tools), list(connections))
    return tools
