"""Postgres storage manager — single owner of engine, session factory, migrations.

Replaces ``noesis_server/infrastructure/database/{engine,dependency,migrations}.py``.
``pg_manager`` is a process singleton built from ``noesis.config.env.DataBaseConfig``.
``noesis.config.checkpointer`` (LangGraph checkpoint, independent DB, psycopg pool)
coexists with this and is NOT merged.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import quote_plus

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, sessionmaker

from noesis.config.env import DataBaseConfig
from noesis.runtime.logging import logger

ASYNC_SQLALCHEMY_DATABASE_URL = (
    f'postgresql+asyncpg://{DataBaseConfig.postgres_user}:{quote_plus(DataBaseConfig.postgres_password)}@'
    f'{DataBaseConfig.postgres_host}:{DataBaseConfig.postgres_port}/{DataBaseConfig.postgres_database}'
)
SYNC_SQLALCHEMY_DATABASE_URL = (
    f'postgresql+psycopg://{DataBaseConfig.postgres_user}:{quote_plus(DataBaseConfig.postgres_password)}@'
    f'{DataBaseConfig.postgres_host}:{DataBaseConfig.postgres_port}/{DataBaseConfig.postgres_database}'
)


class AsyncDatabaseInspector:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def get_table_names(self):
        async with self.engine.connect() as conn:
            return await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())

    async def get_columns(self, table_name):
        async with self.engine.connect() as conn:
            return await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_columns(table_name))

    async def get_table_comment(self, table_name):
        async with self.engine.connect() as conn:
            result = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_comment(table_name))
            return result.get("text", "")

    async def get_primary_key(self, table_name):
        async with self.engine.connect() as conn:
            return await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_pk_constraint(table_name))

    async def get_foreign_keys(self, table_name):
        async with self.engine.connect() as conn:
            return await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_foreign_keys(table_name))


# Alembic config roots (alembic.ini + env.py + versions/ live in noesis/storage/migrations)
_ALEMBIC_INI = Path(__file__).resolve().parents[1] / "migrations" / "alembic.ini"
_INITIAL_REVISION = "202606290001"


def _alembic_config():
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_ALEMBIC_INI.parent))
    cfg.set_main_option("sqlalchemy.url", SYNC_SQLALCHEMY_DATABASE_URL)
    return cfg


def _bootstrap_legacy_schema_stamp(cfg) -> None:
    """init_sql 等旧方式已建表、但未写入 Alembic revision 时，标记 head 避免重复 CREATE TABLE。"""
    engine = create_engine(SYNC_SQLALCHEMY_DATABASE_URL)
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        if "t_user" not in tables:
            return
        from alembic.runtime.migration import MigrationContext

        current = MigrationContext.configure(conn).get_current_revision()
    if current is not None:
        return
    logger.info(
        "检测到已有 schema（revision 为空），标记迁移 {} 为已应用",
        _INITIAL_REVISION,
    )
    from alembic import command

    command.stamp(cfg, "head")


def run_migrations() -> None:
    """执行待应用的 Alembic 迁移（幂等）。"""
    from alembic import command

    cfg = _alembic_config()
    logger.info("执行数据库迁移 alembic upgrade head ...")
    _bootstrap_legacy_schema_stamp(cfg)
    command.upgrade(cfg, "head")
    logger.info("数据库迁移完成")


class PostgresManager:
    """Singleton owner of async/sync engine, session factories, and migrations."""

    def __init__(self) -> None:
        self.async_engine: AsyncEngine | None = None
        self.AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None
        self._sync_engine = None
        self._SyncSessionLocal: sessionmaker[Session] | None = None
        self.inspector: AsyncDatabaseInspector | None = None
        self._engine_ready = False
        self._migrated = False

    def _ensure_engine(self) -> None:
        """Create async engine + session factory + inspector (idempotent).

        Mirrors the old module-level eager creation so importers that read
        ``async_engine`` / ``AsyncSessionLocal`` / ``inspector`` at import time
        keep working. Does NOT touch the DB.
        """
        if self._engine_ready:
            return
        self.async_engine = create_async_engine(
            ASYNC_SQLALCHEMY_DATABASE_URL,
            echo=DataBaseConfig.db_echo,
            max_overflow=DataBaseConfig.db_max_overflow,
            pool_size=DataBaseConfig.db_pool_size,
            pool_recycle=DataBaseConfig.db_pool_recycle,
            pool_timeout=DataBaseConfig.db_pool_timeout,
            pool_pre_ping=True,
        )
        self.AsyncSessionLocal = async_sessionmaker(
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
            bind=self.async_engine,
        )
        self.inspector = AsyncDatabaseInspector(self.async_engine)
        self._engine_ready = True

    def initialize(self) -> None:
        """执行 Alembic 迁移 + 连接校验（应用 lifespan 调用，替代 init_database）。

        Engine creation is lazy-idempotent via ``_ensure_engine``; this method
        only runs migrations and verifies connectivity.
        """
        if self._migrated:
            return
        self._ensure_engine()
        logger.info("🔎 初始化数据库连接...")
        run_migrations()
        self._migrated = True
        logger.info("✅️ 数据库迁移完成")

    async def verify(self) -> None:
        """连接校验（迁移后）。"""
        self._ensure_engine()
        assert self.async_engine is not None
        async with self.async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    @asynccontextmanager
    async def get_async_session_context(self) -> AsyncIterator[AsyncSession]:
        self._ensure_engine()
        assert self.AsyncSessionLocal is not None
        async with self.AsyncSessionLocal() as session:
            yield session

    def get_sync_session(self) -> Session:
        """Agent 工具线程内同步读；避免 asyncio.run 与全局 async 引擎跨 loop 冲突。"""
        if self._SyncSessionLocal is None:
            self._sync_engine = create_engine(
                SYNC_SQLALCHEMY_DATABASE_URL,
                echo=DataBaseConfig.db_echo,
                pool_pre_ping=True,
                pool_size=2,
                max_overflow=2,
                pool_recycle=DataBaseConfig.db_pool_recycle,
                pool_timeout=DataBaseConfig.db_pool_timeout,
            )
            self._SyncSessionLocal = sessionmaker(bind=self._sync_engine, autocommit=False, autoflush=False)
        return self._SyncSessionLocal()

    def get_inspector(self) -> AsyncDatabaseInspector:
        self._ensure_engine()
        assert self.inspector is not None
        return self.inspector

    async def close(self) -> None:
        """关 async + sync engine（补现状未显式关 engine 的缺口）。"""
        if self.async_engine is not None:
            await self.async_engine.dispose()
            self.async_engine = None
        self.AsyncSessionLocal = None
        self.inspector = None
        if self._sync_engine is not None:
            self._sync_engine.dispose()
            self._sync_engine = None
        self._SyncSessionLocal = None
        self._engine_ready = False
        self._migrated = False


pg_manager = PostgresManager()
