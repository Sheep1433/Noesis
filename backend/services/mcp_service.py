"""用户 MCP 配置与目录服务。"""

from __future__ import annotations

import asyncio
import json
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
from config.user_data_paths import get_user_mcp_path
from schemas.mcp_vo import (
    McpConfigFileResponse,
    McpProbeResponse,
    McpServerStatusItemVo,
    McpServerUpsertRequest,
)

_EMPTY_USER_MCP = '{\n  "mcpServers": {}\n}\n'


class McpService:
    @classmethod
    def list_servers(cls, user_id: str | int) -> list[McpServerCatalogItem]:
        return list_merged_servers(user_id)

    @classmethod
    def get_user_config_file(cls, user_id: str | int) -> McpConfigFileResponse:
        path = get_user_mcp_path(user_id)
        exists = path.is_file()
        if exists:
            content = path.read_text(encoding="utf-8")
        else:
            content = _EMPTY_USER_MCP
        return McpConfigFileResponse(
            content=content,
            path_hint=f"users/{user_id}/mcp.json",
            exists=exists,
        )

    @classmethod
    def save_user_config_file(cls, user_id: str | int, content: str) -> McpConfigFileResponse:
        text = (content or "").strip()
        if not text:
            raise ValueError("配置内容不能为空")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 解析失败: {e}") from e
        if not isinstance(raw, dict):
            raise ValueError("顶层必须是对象")
        servers = raw.get("mcpServers")
        if servers is None:
            servers = {}
        if not isinstance(servers, dict):
            raise ValueError("mcpServers 必须是对象")

        validated: dict[str, Any] = {}
        for sid, cfg in servers.items():
            validate_server_id(str(sid))
            if not isinstance(cfg, dict):
                raise ValueError(f"server {sid!r} 配置必须是对象")
            validated[str(sid)] = validate_user_server_config(cfg)

        save_user_mcp_json(user_id, McpJsonConfig(mcpServers=validated))
        clear_mcp_tools_cache()
        logger.info(
            "已写入用户 MCP 配置文件 user_id={} servers={}",
            user_id,
            list(validated),
        )
        return cls.get_user_config_file(user_id)

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

    @classmethod
    async def list_server_status(
        cls,
        user_id: str | int,
        *,
        probe: bool = False,
    ) -> list[McpServerStatusItemVo]:
        catalog = cls.list_servers(user_id)
        if not probe:
            return [
                McpServerStatusItemVo(
                    id=s.id,
                    source=s.source,
                    transport=s.transport,
                    url=s.url,
                    display_name=s.display_name,
                    status="unknown",
                )
                for s in catalog
            ]

        async def _one(s: McpServerCatalogItem) -> McpServerStatusItemVo:
            result = await cls.probe_server(user_id, s.id)
            return McpServerStatusItemVo(
                id=s.id,
                source=s.source,
                transport=s.transport,
                url=s.url,
                display_name=s.display_name,
                status="ok" if result.ok else "error",
                tool_count=result.tool_count,
                message=result.message,
            )

        return list(await asyncio.gather(*[_one(s) for s in catalog]))
