from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncEngine
from sqlalchemy import inspect
from sqlalchemy.orm import DeclarativeBase
from urllib.parse import quote_plus
from config.env import DataBaseConfig

ASYNC_SQLALCHEMY_DATABASE_URL = (
    f'postgresql+asyncpg://{DataBaseConfig.postgres_user}:{quote_plus(DataBaseConfig.postgres_password)}@'
    f'{DataBaseConfig.postgres_host}:{DataBaseConfig.postgres_port}/{DataBaseConfig.postgres_database}'
)

# Alembic 与同步脚本使用（应用运行时仍走 asyncpg）
SYNC_SQLALCHEMY_DATABASE_URL = (
    f'postgresql+psycopg://{DataBaseConfig.postgres_user}:{quote_plus(DataBaseConfig.postgres_password)}@'
    f'{DataBaseConfig.postgres_host}:{DataBaseConfig.postgres_port}/{DataBaseConfig.postgres_database}'
)


class Base(AsyncAttrs, DeclarativeBase):
    pass


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


async_engine = create_async_engine(
    ASYNC_SQLALCHEMY_DATABASE_URL,
    echo=DataBaseConfig.db_echo,
    max_overflow=DataBaseConfig.db_max_overflow,
    pool_size=DataBaseConfig.db_pool_size,
    pool_recycle=DataBaseConfig.db_pool_recycle,
    pool_timeout=DataBaseConfig.db_pool_timeout,
    pool_pre_ping=True,  # 连接前验证，避免使用已断开的连接
)
AsyncSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=async_engine)
inspector = AsyncDatabaseInspector(async_engine)
