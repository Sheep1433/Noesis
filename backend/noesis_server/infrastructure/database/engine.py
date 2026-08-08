"""Re-export ``noesis.storage`` DB engine primitives (transition shim).

The authoritative engine, ``Base``, session factory, and inspector now live in
``noesis.storage.postgres``. This file re-exports them so existing platform
consumers keep importing ``noesis_server.infrastructure.database.engine`` until
they migrate. Removed in F4.
"""
from __future__ import annotations

from noesis.storage.postgres.base import Base
from noesis.storage.postgres.manager import (
    ASYNC_SQLALCHEMY_DATABASE_URL,
    SYNC_SQLALCHEMY_DATABASE_URL,
    AsyncDatabaseInspector,
    pg_manager,
)

# Mirror old eager-creation: engine/session/inspector created on first access,
# NOT at import (avoids touching DB during import). Old code created eagerly at
# module level; pg_manager._ensure_engine() does the same without running
# migrations.
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
