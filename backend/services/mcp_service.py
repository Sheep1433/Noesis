"""用户 MCP 配置与目录服务。"""

from __future__ import annotations

from typing import Any

from agent.mcp.loader import clear_mcp_tools_cache, load_mcp_tools_by_names
from common.logging import logger
from config.mcp_config import (
    McpJsonConfig,
    McpServerCatalogItem,
    list_merged_servers,
    load_user_mcp_json,
    save_user_mcp_json,
    validate_server_id,
    validate_user_server_config,
)
from schemas.mcp_vo import McpProbeResponse, McpServerUpsertRequest


class McpService:
    @classmethod
    def list_servers(cls, user_id: str | int) -> list[McpServerCatalogItem]:
        return list_merged_servers(user_id)

    @classmethod
    def upsert_user_server(
        cls,
        user_id: str | int,
        server_id: str,
        body: McpServerUpsertRequest,
    ) -> McpServerCatalogItem:
        sid = validate_server_id(server_id)
        raw: dict[str, Any] = {
            "transport": body.transport,
            "url": body.url.strip(),
        }
        if body.display_name:
            raw["display_name"] = body.display_name.strip()
        if body.headers:
            raw["headers"] = body.headers
        if body.extra:
            raw.update(body.extra)
        validated = validate_user_server_config(raw)

        cfg = load_user_mcp_json(user_id)
        cfg.mcpServers[sid] = validated
        save_user_mcp_json(user_id, cfg)
        clear_mcp_tools_cache()
        logger.info("已保存用户 MCP server user_id={} id={}", user_id, sid)
        return next(s for s in list_merged_servers(user_id) if s.id == sid)

    @classmethod
    def delete_user_server(cls, user_id: str | int, server_id: str) -> None:
        sid = validate_server_id(server_id)
        cfg = load_user_mcp_json(user_id)
        if sid not in cfg.mcpServers:
            raise KeyError(f"用户 MCP server 不存在: {sid}")
        del cfg.mcpServers[sid]
        save_user_mcp_json(user_id, McpJsonConfig(mcpServers=cfg.mcpServers))
        clear_mcp_tools_cache()
        logger.info("已删除用户 MCP server user_id={} id={}", user_id, sid)

    @classmethod
    async def probe_server(cls, user_id: str | int, server_id: str) -> McpProbeResponse:
        sid = validate_server_id(server_id)
        try:
            tools = await load_mcp_tools_by_names([sid], user_id=user_id, use_cache=False)
            return McpProbeResponse(
                ok=True,
                tool_count=len(tools),
                message=f"连通正常，发现 {len(tools)} 个工具",
            )
        except Exception as e:
            return McpProbeResponse(ok=False, tool_count=0, message=str(e) or "探测失败")
