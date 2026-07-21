"""用户 MCP 配置与目录服务。"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from typing import Any, Literal

from agent.mcp.loader import clear_mcp_tools_cache, format_mcp_error
from common.logging import logger
from config.mcp_config import (
    McpJsonConfig,
    McpServerCatalogItem,
    list_merged_servers,
    list_user_servers,
    load_user_mcp_json,
    materialize_user_mcp_literals,
    resolve_server_connections,
    save_user_mcp_json,
    validate_server_id,
    validate_user_server_config,
)
from config.user_data_paths import ensure_user_mcp_path, get_user_mcp_path
from schemas.mcp_vo import (
    McpConfigFileResponse,
    McpProbeResponse,
    McpServerStatusItemVo,
    McpServerUpsertRequest,
)

# 用户侧默认写字面量（个人 mcp.json 不进 git）；平台 extensions/mcp/mcp.json 才用 ${ENV} 藏密钥
_DEFAULT_USER_MCP = """{
  "mcpServers": {
    "context7": {
      "transport": "streamable_http",
      "url": "https://mcp.context7.com/mcp",
      "display_name": "Context7"
    },
    "remote_ops": {
      "transport": "streamable_http",
      "url": "http://localhost:8000/mcp",
      "display_name": "Remote Ops (SSH)"
    }
  }
}
"""

CatalogScope = Literal["user", "all"]

# 管理页 probe：远程 MCP（如 Context7）冷启动/跨境常需数秒；失败短缓存便于刷新重试
_PROBE_HTTP_TIMEOUT = timedelta(seconds=12)
_PROBE_OK_CACHE_TTL_SEC = 60.0
_PROBE_ERR_CACHE_TTL_SEC = 8.0
_PROBE_CACHE: dict[tuple[str, str], tuple[float, McpProbeResponse]] = {}


def clear_mcp_probe_cache() -> None:
    _PROBE_CACHE.clear()


def _clear_mcp_caches() -> None:
    clear_mcp_tools_cache()
    clear_mcp_probe_cache()


def _probe_error_message(exc: BaseException, *, url: str | None) -> str:
    """把常见连接失败翻成可操作的中文提示。"""
    text = format_mcp_error(exc)
    lower = text.lower()
    target = url or "MCP endpoint"
    if "502" in text or "bad gateway" in lower:
        return (
            f"{target} 返回 502：本机远程运维 MCP 未正常响应。"
            f"请用 START_MCP=1 ./scripts/run.sh dev 启动 extensions/mcp/ssh，"
            f"或检查 NOESIS_MCP_REMOTE_URL。"
        )
    if "connect" in lower and (
        "refused" in lower or "error" in lower or "failed" in lower
    ):
        return (
            f"无法连接 {target}。若为 remote_ops，请启动本地 MCP："
            f"START_MCP=1 ./scripts/run.sh dev"
        )
    if "timed out" in lower or "timeout" in lower:
        return f"连接 {target} 超时"
    return text


class McpService:
    @classmethod
    def list_servers(
        cls,
        user_id: str | int,
        *,
        scope: CatalogScope = "all",
    ) -> list[McpServerCatalogItem]:
        if scope == "user":
            return list_user_servers(user_id)
        return list_merged_servers(user_id)

    @classmethod
    def ensure_user_config_seeded(cls, user_id: str | int) -> None:
        """无文件或 mcpServers 为空时写入推荐模板，使编辑器与状态列表一致。"""
        path = get_user_mcp_path(user_id)
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                servers = raw.get("mcpServers") if isinstance(raw, dict) else None
                if isinstance(servers, dict) and len(servers) > 0:
                    return
            except (OSError, json.JSONDecodeError, TypeError):
                # 损坏或空文件 → 覆盖为模板
                pass
        ensure_user_mcp_path(user_id)
        path.write_text(_DEFAULT_USER_MCP, encoding="utf-8")
        _clear_mcp_caches()
        logger.info("已 seed 用户 MCP 配置 user_id={} path={}", user_id, path)

    @classmethod
    def get_user_config_file(cls, user_id: str | int) -> McpConfigFileResponse:
        cls.ensure_user_config_seeded(user_id)
        if materialize_user_mcp_literals(user_id):
            _clear_mcp_caches()
        path = get_user_mcp_path(user_id)
        content = path.read_text(encoding="utf-8")
        return McpConfigFileResponse(
            content=content,
            path_hint=f"users/{user_id}/mcp.json",
            exists=True,
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
        _clear_mcp_caches()
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
        _clear_mcp_caches()
        logger.info("已保存用户 MCP server user_id={} id={}", user_id, sid)
        return next(s for s in list_user_servers(user_id) if s.id == sid)

    @classmethod
    def delete_user_server(cls, user_id: str | int, server_id: str) -> None:
        sid = validate_server_id(server_id)
        cfg = load_user_mcp_json(user_id)
        if sid not in cfg.mcpServers:
            raise KeyError(f"用户 MCP server 不存在: {sid}")
        del cfg.mcpServers[sid]
        save_user_mcp_json(user_id, McpJsonConfig(mcpServers=cfg.mcpServers))
        _clear_mcp_caches()
        logger.info("已删除用户 MCP server user_id={} id={}", user_id, sid)

    @classmethod
    async def probe_server(
        cls,
        user_id: str | int,
        server_id: str,
        *,
        use_cache: bool = True,
    ) -> McpProbeResponse:
        """真实握手探测；失败不得标为 ok。成功长缓存，失败短缓存便于刷新重试。"""
        from langchain_mcp_adapters.client import MultiServerMCPClient

        sid = validate_server_id(server_id)
        cache_key = (str(user_id), sid)
        now = time.monotonic()
        if use_cache:
            hit = _PROBE_CACHE.get(cache_key)
            if hit and hit[0] > now:
                return hit[1]

        connections = resolve_server_connections([sid], user_id=user_id)
        if sid not in connections:
            result = McpProbeResponse(ok=False, tool_count=0, message="server 未配置")
            _PROBE_CACHE[cache_key] = (now + _PROBE_ERR_CACHE_TTL_SEC, result)
            return result

        cfg = dict(connections[sid])
        url = str(cfg.get("url") or "") or None
        # 管理页探测超时（默认适配器 30s 过久；Context7 跨境偶发 >4s）
        cfg.setdefault("timeout", _PROBE_HTTP_TIMEOUT)
        try:
            client = MultiServerMCPClient({sid: cfg})
            tools = await asyncio.wait_for(
                client.get_tools(),
                timeout=_PROBE_HTTP_TIMEOUT.total_seconds() + 1.0,
            )
            result = McpProbeResponse(
                ok=True,
                tool_count=len(tools),
                message=f"连通正常，发现 {len(tools)} 个工具",
            )
        except TimeoutError:
            result = McpProbeResponse(
                ok=False,
                tool_count=0,
                message=(
                    f"探测超时（>{_PROBE_HTTP_TIMEOUT.total_seconds():.0f}s），"
                    f"目标={url or sid}。网络较慢时可再点刷新。"
                ),
            )
        except Exception as e:
            result = McpProbeResponse(
                ok=False,
                tool_count=0,
                message=_probe_error_message(e, url=url),
            )

        ttl = _PROBE_OK_CACHE_TTL_SEC if result.ok else _PROBE_ERR_CACHE_TTL_SEC
        _PROBE_CACHE[cache_key] = (now + ttl, result)
        return result

    @classmethod
    async def list_server_status(
        cls,
        user_id: str | int,
        *,
        probe: bool = False,
        scope: CatalogScope = "user",
    ) -> list[McpServerStatusItemVo]:
        if scope == "user":
            cls.ensure_user_config_seeded(user_id)
        catalog = cls.list_servers(user_id, scope=scope)
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

        # 并行探测：总耗时 ≈ max(各 server)，而非 sum
        return list(await asyncio.gather(*[_one(s) for s in catalog]))
