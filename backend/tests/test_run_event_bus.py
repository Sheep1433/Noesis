"""RunEventBus Fan-out 单测。"""
from __future__ import annotations

import asyncio

import pytest

from domain.chat.delivery.bus import RunEventBus
from domain.chat.delivery.events import RunCompleted, WireFrame
from domain.chat.delivery.sse import iter_sse_from_bus


@pytest.mark.asyncio
async def test_bus_fanout_two_subscribers_see_completed() -> None:
    bus = RunEventBus()
    run_id = "r1"
    q1 = bus.subscribe_queue(run_id)
    q2 = bus.subscribe_queue(run_id)

    await bus.publish(run_id, WireFrame(event="text-delta", data={"type": "text-delta", "delta": "hi"}))
    await bus.publish(run_id, RunCompleted(finish_reason="stop"))
    await bus.publish_end(run_id)

    seen1 = []
    seen2 = []
    async for ev in bus.iter_events(run_id, queue=q1):
        seen1.append(ev)
    async for ev in bus.iter_events(run_id, queue=q2):
        seen2.append(ev)

    assert any(isinstance(x, RunCompleted) for x in seen1)
    assert any(isinstance(x, RunCompleted) for x in seen2)
    assert len(seen1) == len(seen2) == 2


@pytest.mark.asyncio
async def test_sse_delivery_keepalive_not_from_bus() -> None:
    """keepalive 由 SseDelivery 注入，总线无心跳事件。"""
    bus = RunEventBus()
    run_id = "r-hb"
    q = bus.subscribe_queue(run_id)

    async def late_publish() -> None:
        await asyncio.sleep(0.08)
        await bus.publish(
            run_id,
            WireFrame(event="finish", data={"type": "finish", "finish_reason": "stop"}),
        )
        await bus.publish_end(run_id)

    task = asyncio.create_task(late_publish())
    lines: list[str] = []
    async for line in iter_sse_from_bus(bus, run_id, keepalive_seconds=0.03, queue=q):
        lines.append(line)
    await task

    assert any(line.startswith(": keepalive") for line in lines)
    assert any("event: finish" in line for line in lines)
    # 总线侧不应曾 publish 过 keepalive（仅 WireFrame + end）
