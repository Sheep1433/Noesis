"""LangGraph 共享 SQLite checkpointer 生命周期管理。"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from config.env import get_config
from common.logging import logger
from common.paths import resolve_backend_relative

_conn: aiosqlite.Connection | None = None
_saver: AsyncSqliteSaver | None = None


def resolve_checkpoint_db_path() -> Path:
    path = resolve_backend_relative(get_config.get_checkpoint_config().db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


async def init_checkpointer() -> AsyncSqliteSaver:
    """在应用启动时初始化 SQLite checkpointer。"""
    global _conn, _saver
    if _saver is not None:
        return _saver

    db_path = resolve_checkpoint_db_path()
    _conn = await aiosqlite.connect(str(db_path))
    _saver = AsyncSqliteSaver(_conn)
    await _saver.setup()
    logger.info(f"LangGraph SQLite checkpointer 已初始化: {db_path}")
    return _saver


async def close_checkpointer() -> None:
    """在应用关闭时释放 SQLite 连接。"""
    global _conn, _saver
    if _conn is not None:
        await _conn.close()
    _conn = None
    _saver = None
    logger.info("LangGraph SQLite checkpointer 已关闭")


def get_checkpointer() -> AsyncSqliteSaver:
    """获取已初始化的 checkpointer 实例。"""
    if _saver is None:
        raise RuntimeError(
            "LangGraph checkpointer 未初始化，请确保应用 lifespan 已调用 init_checkpointer()"
        )
    return _saver
