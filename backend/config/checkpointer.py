"""LangGraph 共享 PostgreSQL checkpointer 生命周期管理。"""

from __future__ import annotations

from urllib.parse import quote_plus

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from common.logging import logger
from config.env import DataBaseConfig, get_config

_pool: AsyncConnectionPool | None = None
_saver: AsyncPostgresSaver | None = None


def checkpoint_connection_url() -> str:
    """返回供 psycopg 使用的独立 checkpoint 数据库连接串。"""
    database = get_config.get_checkpoint_config().postgres_database
    return (
        f"postgresql://{DataBaseConfig.postgres_user}:{quote_plus(DataBaseConfig.postgres_password)}@"
        f"{DataBaseConfig.postgres_host}:{DataBaseConfig.postgres_port}/{database}"
    )


async def init_checkpointer() -> AsyncPostgresSaver:
    global _pool, _saver
    if _saver is not None:
        return _saver

    _pool = AsyncConnectionPool(
        conninfo=checkpoint_connection_url(),
        kwargs={"autocommit": True, "prepare_threshold": 0},
        open=False,
    )
    await _pool.open()
    _saver = AsyncPostgresSaver(_pool)
    await _saver.setup()
    logger.info("LangGraph PostgreSQL checkpointer 已初始化")
    return _saver


async def close_checkpointer() -> None:
    global _pool, _saver
    if _pool is not None:
        await _pool.close()
    _pool = None
    _saver = None
    logger.info("LangGraph PostgreSQL checkpointer 已关闭")


def get_checkpointer() -> AsyncPostgresSaver:
    if _saver is None:
        raise RuntimeError("LangGraph checkpointer 未初始化，请确保应用 lifespan 已调用 init_checkpointer()")
    return _saver
