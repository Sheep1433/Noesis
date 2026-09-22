"""隔离事件循环：全部后台任务共享的执行舞台。

跨线程挂定时器必须经 _loop_timer_arm 调度回本线程——asyncio loop
非线程安全，直接 call_later 会破坏定时器堆序（曾致看门狗提前弹出）。
每任务独立循环为目标态（设计文档 Phase 4b），当前保持共享循环。
"""
from __future__ import annotations

import asyncio
import contextvars
import threading
from concurrent.futures import Future
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from noesis.agents.background.jobs.registry import _TaskEntry

# ---------------------------------------------------------------------------
# 隔离事件循环：后台任务不是主 run 任务树的子节点，主 run 结束不回收
# ---------------------------------------------------------------------------

_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_thread: Optional[threading.Thread] = None
_loop_lock = threading.Lock()

def _run_isolated_loop(ready: threading.Event) -> None:
    global _loop
    loop = asyncio.new_event_loop()
    _loop = loop
    asyncio.set_event_loop(loop)
    ready.set()
    try:
        loop.run_forever()
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()

def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop_thread
    with _loop_lock:
        if _loop is not None and not _loop.is_closed():
            return _loop
        ready = threading.Event()
        _loop_thread = threading.Thread(
            target=_run_isolated_loop,
            args=(ready,),
            daemon=True,
            name="noesis-bg-subagent-loop",
        )
        _loop_thread.start()
        ready.wait(timeout=5)
        assert _loop is not None
        return _loop

def _loop_timer_arm(entry: _TaskEntry, attr: str, delay: float, callback) -> None:
    """在隔离 loop 线程内挂定时器（先摘旧句柄，新句柄写回 entry.<attr>）。

    cancel / start 在主线程触发挂载：直接
    ``loop.call_later`` 是跨线程 heappush——asyncio loop 非线程安全，
    与 loop 自身的堆操作竞态会破坏堆序（曾致新看门狗被提前 ~11 分钟
    弹出）。挂载统一调度
    回 loop 线程执行；摘除保持即时 cancel（TimerHandle 置标志即生效）。
    """
    loop = _ensure_loop()

    def _arm() -> None:
        prev = getattr(entry, attr)
        if prev is not None:
            prev.cancel()
        setattr(entry, attr, loop.call_later(delay, callback, entry))

    if threading.current_thread() is _loop_thread:
        _arm()
    else:
        loop.call_soon_threadsafe(_arm)

def shutdown_loop() -> None:
    """进程退出时停掉隔离 loop（FastAPI lifespan 调用）。"""
    global _loop, _loop_thread
    with _loop_lock:
        loop = _loop
        _loop = None
        _loop_thread = None
    if loop is not None and not loop.is_closed():
        loop.call_soon_threadsafe(loop.stop)

def _submit_isolated(loop: asyncio.AbstractEventLoop, coro) -> Future:
    """调度协程到隔离 loop，并在干净的 contextvars 中执行。

    ``run_coroutine_threadsafe`` 经 ``call_soon_threadsafe`` 复制调用线程的
    contextvars；而调度点常在父 run 的 astream_events 追踪上下文内
    （start_async_task 等工具执行期间）。子 Agent 若继承父
    tracer，其 LLM/工具事件会泄入父事件流——曾在父消息尾部生成幽灵
    工具 part 并触发「本轮未完成」误报。这里把真实工作放进空 Context
    的内层 Task 切断继承；取消经 await 传播，Future 语义与
    run_coroutine_threadsafe 一致。子 Agent 自身依赖（backend/
    checkpointer）均经闭包传参，不依赖 contextvars。
    """
    return asyncio.run_coroutine_threadsafe(_run_in_clean_context(coro), loop)

async def _run_in_clean_context(coro):
    task = asyncio.get_running_loop().create_task(coro, context=contextvars.Context())
    try:
        return await task
    finally:
        if not task.done():
            task.cancel()
