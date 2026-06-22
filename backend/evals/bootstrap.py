"""离线评测运行时依赖初始化（不走 FastAPI lifespan）。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from langgraph.checkpoint.memory import MemorySaver

import config.checkpointer as checkpointer_module


@asynccontextmanager
async def eval_runtime() -> AsyncIterator[None]:
    """注入内存 checkpointer，与线上 SQLite 隔离；评测无需跨轮/重启恢复。"""
    previous = checkpointer_module._saver
    checkpointer_module._saver = MemorySaver()
    try:
        yield
    finally:
        checkpointer_module._saver = previous
