"""平台 DB engine 基础设施。

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
)

pg_manager._ensure_engine()

async_engine = pg_manager.async_engine
AsyncSessionLocal = pg_manager.AsyncSessionLocal
inspector = pg_manager.inspector

__all__ = [
    "Base",
    "ASYNC_SQLALCHEMY_DATABASE_URL",
    "SYNC_SQLALCHEMY_DATABASE_URL",
    "AsyncDatabaseInspector",
    "async_engine",
    "AsyncSessionLocal",
    "inspector",
    "pg_manager",
]
