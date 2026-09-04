"""平台 DB 基础设施：请求 session dependency 与迁移入口。

委托 ``noesis.storage.postgres.manager.pg_manager``，为平台 HTTP 层
（API Depends）提供请求级 session。核心包和启动代码直接使用
``pg_manager``，避免缓存会在关闭后失效的 engine/session factory 别名。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.storage.postgres.base import Base
from noesis.storage.postgres.manager import (
    ASYNC_SQLALCHEMY_DATABASE_URL,
    SYNC_SQLALCHEMY_DATABASE_URL,
    AsyncDatabaseInspector,
    pg_manager,
    run_migrations,
)

async def get_db():
    """每一个请求处理完毕后会关闭当前连接，不同的请求使用不同的连接。"""
    async with pg_manager.get_async_session_context() as current_db:
        yield current_db


@asynccontextmanager
async def sse_prefetch_db() -> AsyncIterator[AsyncSession]:
    """SSE 端点预取专用短命会话。

    yield 依赖（get_db）的清理要等响应发送完毕才执行，而
    StreamingResponse 直到客户端断开才结束——普通依赖会让会话以
    idle-in-transaction 状态钉住池连接整个流生命周期（阻塞 DDL、
    多标签页耗尽连接池）。SSE 端点必须在返回流之前完成全部预取并
    退出本上下文；事件生成器只允许使用内存队列/订阅句柄。
    """
    async with pg_manager.get_async_session_context() as db:
        yield db


def get_inspector():
    return pg_manager.get_inspector()


async def init_database():
    """应用启动时执行 Alembic 迁移并校验数据库连接。"""
    pg_manager.initialize()
    await pg_manager.verify()


__all__ = [
    "Base",
    "ASYNC_SQLALCHEMY_DATABASE_URL",
    "SYNC_SQLALCHEMY_DATABASE_URL",
    "AsyncDatabaseInspector",
    "pg_manager",
    "run_migrations",
    "get_db",
    "sse_prefetch_db",
    "get_inspector",
    "init_database",
]


if __name__ == "__main__":
    run_migrations()
