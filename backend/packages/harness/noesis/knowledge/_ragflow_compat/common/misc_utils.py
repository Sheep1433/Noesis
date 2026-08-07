import asyncio
import contextvars
import functools
from concurrent.futures import ThreadPoolExecutor

_executor: ThreadPoolExecutor | None = None


def pip_install_torch() -> None:
    return None


def _thread_pool_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=8)
    return _executor


async def thread_pool_exec(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    if kwargs:
        inner = functools.partial(func, *args, **kwargs)
        return await loop.run_in_executor(_thread_pool_executor(), ctx.run, inner)
    return await loop.run_in_executor(_thread_pool_executor(), ctx.run, func, *args)
