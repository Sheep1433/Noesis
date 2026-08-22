"""主事件循环注册与跨线程调度。

后台子 Agent 执行器跑在隔离线程 loop；其终态触发的自动续跑（continuation
run）需要访问主 loop 的资源（SQLAlchemy 引擎连接池、RunManager），必须把
协程调度回主 loop 执行。lifespan 启动时捕获主 loop 引用。
"""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
from typing import Any, Coroutine

from noesis.runtime.logging import logger

_MAIN_LOOP: asyncio.AbstractEventLoop | None = None


def capture_main_loop() -> None:
    """进程主 loop 注册（FastAPI lifespan 启动时调用）。"""
    global _MAIN_LOOP
    _MAIN_LOOP = asyncio.get_running_loop()


def run_on_main_loop(coro: Coroutine[Any, Any, Any], *, name: str = "") -> Future | None:
    """把协程投递到主 loop；返回跨线程 Future，调用方可选择等待。"""
    loop = _MAIN_LOOP
    if loop is None or loop.is_closed():
        coro.close()
        logger.debug("main loop unavailable, skip scheduled coro {}", name)
        return None
    try:
        return asyncio.run_coroutine_threadsafe(coro, loop)
    except RuntimeError:
        coro.close()
        logger.debug("main loop scheduling failed, skip {}", name)
        return None
