"""super_agent 子 Agent DB 回调的主 loop 调度测试。

_pg_manager 池绑定主 loop；三个子 Agent 回调（建子会话/删子会话/拒 run）
必须在被投递到主 loop 的协程里执行 DB 操作，否则在 executor 隔离 loop
上直连会出 asyncpg 跨 loop 错误（_create_追加消息_run 的冷恢复前科）。
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from noesis.agents.super_agent import _db_on_main_loop


@pytest.mark.asyncio
async def test_db_on_main_loop_falls_back_without_main_loop(monkeypatch):
    """主 loop 未注册（评测/CLI 单 loop 进程）：退回当前 loop 直连。

    _MAIN_LOOP 用 monkeypatch 置空——全量套件里其他用例可能注册过主
    loop，直接断言 None 是顺序脆弱的。
    """
    from noesis.runtime import main_loop as ml

    monkeypatch.setattr(ml, "_MAIN_LOOP", None)
    result = await _db_on_main_loop(lambda: _async_value(42), name="t")
    assert result == 42


@pytest.mark.asyncio
async def test_db_on_main_loop_dispatches_to_registered_loop():
    """主 loop 已注册：DB 协程在被注册的 loop 上执行，而非调用方 loop。"""
    from noesis.runtime import main_loop as ml

    started = threading.Event()
    done = threading.Event()
    observed: dict[str, object] = {}

    original = ml._MAIN_LOOP

    def _run_registered_loop() -> None:
        loop = asyncio.new_event_loop()
        ml._MAIN_LOOP = loop
        try:
            started.set()
            loop.run_until_complete(asyncio.sleep(3600))
        finally:
            ml._MAIN_LOOP = original

    thread = threading.Thread(target=_run_registered_loop, daemon=True)
    thread.start()
    try:
        assert started.wait(5)

        async def _probe() -> str:
            observed["loop"] = asyncio.get_running_loop()
            return "ok"

        # 从测试 loop（非主 loop）发起——协程应跑在注册的主 loop 上
        result = await _db_on_main_loop(_probe, name="probe")
        assert result == "ok"
        assert observed["loop"] is ml._MAIN_LOOP
        assert observed["loop"] is not asyncio.get_running_loop()
    finally:
        # 收尾注册 loop
        ml._MAIN_LOOP.call_soon_threadsafe(done.set)
        thread.join(timeout=5)


async def _async_value(value: int) -> int:
    await asyncio.sleep(0)
    return value
