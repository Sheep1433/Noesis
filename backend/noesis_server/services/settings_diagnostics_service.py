"""Concurrent, bounded, user-safe settings diagnostics."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from noesis_server.domain.chat.delivery.channel_health import channel_health
from noesis_server.models.settings_models import TUserProviderConnection
from noesis_server.infrastructure.database.engine import AsyncSessionLocal
from noesis_server.services.messaging_channel_service import MessagingChannelService

Check = Callable[[], Awaitable[tuple[str, str, str | None]]]


class SettingsDiagnosticsService:
    @staticmethod
    async def _run(name: str, check: Check, timeout: float = 2.0) -> dict:
        correlation_id = uuid.uuid4().hex[:12]
        try:
            status, message, action = await asyncio.wait_for(check(), timeout=timeout)
        except TimeoutError:
            status, message, action = "unavailable", "检查超时，请稍后重试", "retry"
        except Exception:
            status, message, action = "unavailable", "暂时无法完成检查", "retry"
        return {"key": name, "status": status, "checked_at": int(time.time() * 1000), "message": message, "action_code": action, "correlation_id": correlation_id}

    @classmethod
    async def diagnose(cls, db: AsyncSession, user_id: int) -> dict:
        async def database():
            async with AsyncSessionLocal() as check_db:
                await check_db.execute(text("SELECT 1"))
            return "healthy", "数据服务正常", None

        async def providers():
            from noesis_server.services.provider_service import ProviderService
            async with AsyncSessionLocal() as check_db:
                rows = list((await check_db.execute(select(TUserProviderConnection).where(TUserProviderConnection.user_id == user_id, TUserProviderConnection.enabled.is_(True)))).scalars())
                if not rows:
                    return "unknown", "尚未启用模型连接", "configure_provider"
                results = await asyncio.gather(*(ProviderService.probe(check_db, user_id, row.id) for row in rows))
            return ("healthy", "模型连接正常", None) if all(item.ok for item in results) else ("degraded", "部分模型连接需要检查", "check_provider")

        async def mcp():
            from noesis_server.services.mcp_service import McpService
            servers = McpService.list_servers(user_id, scope="user")
            if not servers:
                return "unknown", "尚未配置扩展服务", "configure_mcp"
            results = await asyncio.gather(*(McpService.probe_server(user_id, server.id) for server in servers if server.enabled))
            return ("healthy", "扩展服务连接正常", None) if results and all(item.ok for item in results) else ("degraded", "部分扩展服务需要检查", "check_mcp")

        async def scheduler():
            from noesis_server.services import scheduled_task_scheduler
            task = scheduled_task_scheduler._task
            return ("healthy", "自动化调度正常", None) if task and not task.done() else ("degraded", "自动化调度当前未运行", "retry")

        async def channels():
            configs = MessagingChannelService.list_channels(user_id)
            if not configs:
                return "unknown", "尚未配置通讯通道", "configure_channel"
            statuses = [channel_health.get(user_id, item["channel_id"])["status"] for item in configs if item["enabled"]]
            return ("healthy", "通讯通道运行正常", None) if statuses and all(item == "healthy" for item in statuses) else ("degraded", "部分通道需要检查", "check_channel")

        async def checkpoint():
            from noesis.config.checkpointer import get_checkpointer
            get_checkpointer()
            return "healthy", "会话状态服务正常", None

        async def qdrant():
            from noesis_server.kb.qdrant import is_qdrant_connected
            return ("healthy", "知识检索服务正常", None) if is_qdrant_connected() else ("unavailable", "知识检索服务暂不可用", "retry")

        async def sandbox():
            from noesis.backends import sandbox_lifecycle
            if not hasattr(sandbox_lifecycle, "agent_sandbox_session") and not hasattr(sandbox_lifecycle, "shutdown_sandboxes"):
                return "unknown", "执行环境状态未知", "retry"
            return "healthy", "执行环境可用", None

        checks = {"models": providers, "mcp": mcp, "scheduler": scheduler, "channels": channels, "database": database, "checkpoint": checkpoint, "qdrant": qdrant, "sandbox": sandbox}
        items = await asyncio.gather(*(cls._run(name, check) for name, check in checks.items()))
        overall = "healthy" if all(item["status"] in {"healthy", "unknown"} for item in items) else "degraded"
        return {"status": overall, "checked_at": int(time.time() * 1000), "items": items}
