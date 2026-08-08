"""Re-export ``noesis.storage`` DB session/dependency primitives (transition shim).

Authoritative manager lives in ``noesis.storage.postgres.manager``. This file
keeps the platform-facing ``get_db`` / ``init_database`` / ``get_inspector`` API
stable for ``Depends(get_db)`` call sites and lifespan. Removed in F4.
"""
from __future__ import annotations

from noesis.storage.postgres.manager import pg_manager


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
