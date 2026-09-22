"""信令跨 worker 广播回归（enable-distributed-sse-pubsub task 4.7/4.8）。

两类本地信令总线 + bg 任务面板事件经 SignalBridge 桥接：本地 fan-out 不变、
发布同时上 bus、远端泵投回本地（回声抑制）；多 Tab 连 follower 与直连
leader 表现一致；memory 模式（不装配桥）零行为变化。
"""

from __future__ import annotations

import asyncio

import pytest

from noesis.chat.runs.bus import InMemoryRunBus
from noesis.chat.runs.signal_bridge import SignalBridge


async def _get(queue, timeout: float = 2.0):
    return await asyncio.wait_for(queue.get(), timeout=timeout)


async def _wait_pumps(bridge: SignalBridge, count: int = 1) -> None:
    for _ in range(200):
        if bridge.pump_count >= count:
            return
        await asyncio.sleep(0.005)


async def _wait_signal_subscribed(bus, scope: str, key: str) -> None:
    """等泵的 bus 订阅真正建立（pump 任务被调度并完成 subscribe）。"""
    for _ in range(400):
        if (scope, key) in getattr(bus, "_signal_channels", {}):
            await asyncio.sleep(0)
            return
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_bg_events_cross_worker_delivery_and_pump_lifecycle() -> None:
    """bg 任务面板/目录流：远端发布 → 本地订阅收到；末订阅者离开释放泵。"""
    from noesis.agents.background.jobs import events as bg_events

    bus = InMemoryRunBus(envelope_payload_max_bytes=64 * 1024)
    bridge = SignalBridge(bus=bus, origin="worker-a")
    bg_events.configure_bg_signal_bridge(bridge)
    try:
        q1 = bg_events.subscribe_bg_events("session-1", "user-1")
        q2 = bg_events.subscribe_bg_events("session-1", "user-1")  # 同 worker 多 Tab
        await _wait_signal_subscribed(bus, "bg-tasks", "session-1")

        # 模拟另一 worker（leader）发布任务终态到 bg 通道
        await bus.publish_signal(
            "bg-tasks", "session-1",
            {"event": "terminal", "task": {"task_id": "t1", "status": "completed"}},
            origin="worker-b",
        )
        assert (await _get(q1))["event"] == "terminal"
        assert (await _get(q2))["event"] == "terminal", "多 Tab fan-out 一致"

        # 本地发布路径也上桥（远端订阅者可见）
        spy_sub = await bus.subscribe_signals("bg-tasks", "session-2")
        await spy_sub.ready()
        q3 = bg_events.subscribe_bg_events("session-2", "user-1")
        bg_events.publish_session_event(
            "session-2", "user-1", {"event": "child-session", "child": {"session_id": "c1"}}
        )
        remote = await asyncio.wait_for(spy_sub.__aiter__().__anext__(), timeout=2)
        assert remote["payload"]["event"] == "child-session"
        assert (await _get(q3))["event"] == "child-session", "本地发布本地也收到"

        # 末订阅者离开：泵释放
        bg_events.unsubscribe_bg_events("session-1", q1)
        bg_events.unsubscribe_bg_events("session-1", q2)
        for _ in range(200):
            if bridge.pump_count == 1:  # session-2 的泵仍在
                break
            await asyncio.sleep(0.005)
        assert bridge.pump_count == 1, "session-1 的远端泵应已释放"
        await spy_sub.close()
    finally:
        bg_events.configure_bg_signal_bridge(None)
        await bridge.close()
        await bus.close()


@pytest.mark.asyncio
async def test_memory_mode_without_bridge_unchanged() -> None:
    """未装配桥（memory 模式）：纯本地投递，无跨进程副作用。"""
    from noesis.agents.background.jobs import events as bg_events

    q = bg_events.subscribe_bg_events("session-1", "user-1")
    bg_events._deliver_local("session-1", {"type": "x"})
    assert (await _get(q))["type"] == "x"
    bg_events.unsubscribe_bg_events("session-1", q)
