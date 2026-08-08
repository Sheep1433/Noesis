"""平台 DB 基础设施：session dependency + engine 委托 + 迁移入口。

委托 ``noesis.storage.postgres.manager.pg_manager``，为平台 HTTP 层
（API Depends / lifespan / middleware / bootstrap）提供 session factory
与 engine 符号。harness 内核直接用 ``pg_manager``，不经此层。
"""
from __future__ import annotations

from noesis.storage.postgres.base import Base
from noesis.storage.postgres.manager import (
    ASYNC_SQLALCHEMY_DATABASE_URL,
    SYNC_SQLALCHEMY_DATABASE_URL,
    AsyncDatabaseInspector,
    pg_manager,
    run_migrations,
)

pg_manager._ensure_engine()

async_engine = pg_manager.async_engine
AsyncSessionLocal = pg_manager.AsyncSessionLocal
inspector = pg_manager.inspector


async def get_db():
    """每一个请求处理完毕后会关闭当前连接，不同的请求使用不同的连接。"""
    async with pg_manager.get_async_session_context() as current_db:
        yield current_db


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
    "async_engine",
    "AsyncSessionLocal",
    "inspector",
    "pg_manager",
    "run_migrations",
    "get_db",
    "get_inspector",
    "init_database",
]


if __name__ == "__main__":
    run_migrations()
