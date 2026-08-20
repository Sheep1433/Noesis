"""LangGraph 共享 PostgreSQL checkpointer 生命周期管理。"""

from __future__ import annotations

import asyncio

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator
from urllib.parse import quote_plus

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from noesis.runtime.logging import logger
from noesis.config.env import DataBaseConfig, get_config

_pool: AsyncConnectionPool | None = None
_saver: AsyncPostgresSaver | None = None
# 后台子 Agent 隔离 loop 专用：连接池绑定隔离 loop，不复用主 loop 实例
_isolated_pool: AsyncConnectionPool | None = None
_isolated_saver: AsyncPostgresSaver | None = None
_checkpointer_override: ContextVar[Any | None] = ContextVar(
    "noesis_checkpointer_override", default=None
)


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


async def create_isolated_checkpointer() -> AsyncPostgresSaver:
    """为后台子 Agent 隔离 loop 创建专用 checkpointer（首次调用时惰性建池）。

    psycopg AsyncConnectionPool 绑定创建时的 event loop；后台任务在隔离
    线程 loop 上运行，复用主 loop 的池会 cross-loop 报错。此 saver 与主
    saver 共用同一 checkpoint 数据库，实例互相独立。
    """
    global _isolated_pool, _isolated_saver
    if _isolated_saver is not None:
        return _isolated_saver
    _isolated_pool = AsyncConnectionPool(
        conninfo=checkpoint_connection_url(),
        kwargs={"autocommit": True, "prepare_threshold": 0},
        open=False,
    )
    await _isolated_pool.open()
    _isolated_saver = AsyncPostgresSaver(_isolated_pool)
    await _isolated_saver.setup()
    logger.info("后台子 Agent 隔离 checkpointer 已初始化")
    return _isolated_saver


def close_isolated_checkpointer_on_loop() -> None:
    """在隔离 loop 停止前关闭其连接池（executor.shutdown 调用）。

    必须在隔离 loop 仍在运行时调用：psycopg 池绑定创建它的 loop，
    在别的 loop（或已停 loop）上 close 会失败。
    """
    global _isolated_pool, _isolated_saver
    pool = _isolated_pool
    _isolated_pool = None
    _isolated_saver = None
    if pool is None:
        return
    from noesis.agents.subagents.executor import _ensure_loop

    loop = _ensure_loop()

    async def _close() -> None:
        try:
            await pool.close()
        except Exception:
            logger.warning("后台子 Agent 隔离连接池关闭异常（忽略）")

    try:
        asyncio.run_coroutine_threadsafe(_close(), loop).result(timeout=5)
    except Exception:
        logger.warning("后台子 Agent 隔离连接池关闭调度失败（忽略）")


async def close_checkpointer() -> None:
    global _pool, _saver, _isolated_pool, _isolated_saver
    if _pool is not None:
        await _pool.close()
    _pool = None
    _saver = None
    if _isolated_pool is not None:
        try:
            # 隔离池绑定隔离 loop，需切到该 loop 关闭
            from noesis.agents.subagents.executor import _loop

            if _loop is not None and not _loop.is_closed():
                fut = asyncio.run_coroutine_threadsafe(_isolated_pool.close(), _loop)
                fut.result(timeout=5)
            else:
                await _isolated_pool.close()
        except Exception:
            # 隔离 loop 已停或池已失效：退出路径不因清理失败而中断
            logger.warning("后台子 Agent 隔离 checkpointer 关闭异常（忽略）")
    _isolated_pool = None
    _isolated_saver = None
    logger.info("LangGraph PostgreSQL checkpointer 已关闭")


@contextmanager
def temporary_checkpointer(checkpointer: Any) -> Iterator[None]:
    """Temporarily override the checkpointer in the current execution context."""
    token = _checkpointer_override.set(checkpointer)
    try:
        yield
    finally:
        _checkpointer_override.reset(token)


def get_checkpointer() -> Any:
    override = _checkpointer_override.get()
    if override is not None:
        return override
    if _saver is None:
        raise RuntimeError("LangGraph checkpointer 未初始化，请确保应用 lifespan 已调用 init_checkpointer()")
    return _saver
