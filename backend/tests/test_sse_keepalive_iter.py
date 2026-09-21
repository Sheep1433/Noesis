"""SSE 注释保活：投递层 iter_sse_from_bus 注入；raw 生产者侧不进总线心跳。"""
from __future__ import annotations

import asyncio

import pytest

from noesis.chat.delivery.bus import RunEventBus
from noesis.chat.delivery.events import WireFrame
from noesis.chat.delivery.sse import SSE_COMMENT_KEEPALIVE, iter_sse_from_bus
from noesis.chat.event_mapping.bridge import (
    END_SENTINEL,
    HEARTBEAT_SENTINEL,
    MemoryStreamBridge,
    iter_bridge_events,
)


@pytest.mark.asyncio
async def test_keepalive_from_bus_idle_timeout() -> None:
    async def slow_producer(bus: RunEventBus, run_id: str) -> None:
        await asyncio.sleep(0.12)
        await bus.publish(run_id, WireFrame(event="text-delta", data={"text_delta": "ok"}))
        await bus.publish_end(run_id)

    bus = RunEventBus()
    run_id = "run-hb"
    lines: list[str] = []
    task = asyncio.create_task(slow_producer(bus, run_id))
    async for line in iter_sse_from_bus(bus, run_id, keepalive_seconds=0.02):
        lines.append(line)
    await task

    assert any(line.startswith(": keepalive") or line == SSE_COMMENT_KEEPALIVE for line in lines)
    assert any("text-delta" in line for line in lines)


@pytest.mark.asyncio
async def test_keepalive_disabled_zero_interval() -> None:
    bus = RunEventBus()
    run_id = "run-off"
    # 先订阅再发布：总线只广播给订阅时已存在的队列，避免事件丢失导致等待
    queue = bus.subscribe_queue(run_id)
    await bus.publish(run_id, WireFrame(event="text-delta", data={"text_delta": "ok"}))
    await bus.publish_end(run_id)

    lines = [
        line
        async for line in iter_sse_from_bus(
            bus, run_id, keepalive_seconds=0, queue=queue
        )
    ]

    assert not any(line.startswith(": keepalive") for line in lines)
    assert any("text-delta" in line for line in lines)


@pytest.mark.asyncio
async def test_raw_bridge_zero_keepalive_no_heartbeat_sentinel() -> None:
    """生产者侧 keepalive=0 时不应产出 HEARTBEAT_SENTINEL（心跳仅在投递层）。"""

    async def slow_gen():
        await asyncio.sleep(0.08)
        yield {"type": "ok"}

    mem = MemoryStreamBridge()
    seen: list = []
    async for item in iter_bridge_events(
        mem,
        "run-raw",
        slow_gen(),
        keepalive_seconds=0,
    ):
        seen.append(item)

    assert not any(x is HEARTBEAT_SENTINEL for x in seen)
    assert any(isinstance(x, dict) and x.get("type") == "ok" for x in seen)
    assert any(x is END_SENTINEL for x in seen)


@pytest.mark.asyncio
async def test_bus_idle_wait_does_not_cancel_slow_producer() -> None:
    cancelled = False

    async def slow_producer(bus: RunEventBus, run_id: str) -> None:
        nonlocal cancelled
        try:
            await asyncio.sleep(0.1)
            await bus.publish(run_id, WireFrame(event="text-delta", data={"text_delta": "ok"}))
            await bus.publish_end(run_id)
        except asyncio.CancelledError:
            cancelled = True
            raise

    bus = RunEventBus()
    run_id = "run-prod"
    lines: list[str] = []
    task = asyncio.create_task(slow_producer(bus, run_id))
    async for line in iter_sse_from_bus(bus, run_id, keepalive_seconds=0.03):
        lines.append(line)
    await task

    assert not cancelled
    assert any("ok" in line or "text" in line for line in lines)
