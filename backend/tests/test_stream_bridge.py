"""MemoryStreamBridge 单元测试。"""
from __future__ import annotations

import asyncio

import pytest

from utils.stream_bridge import (
    END_SENTINEL,
    HEARTBEAT_SENTINEL,
    StreamBridgeError,
    MemoryStreamBridge,
    iter_bridge_events,
    run_stream_producer,
)


@pytest.mark.asyncio
async def test_publish_subscribe_delivers_items_in_order() -> None:
    bridge = MemoryStreamBridge()
    run_id = "run-1"

    async def producer() -> None:
        await bridge.publish(run_id, {"type": "a"})
        await bridge.publish(run_id, {"type": "b"})
        await bridge.publish_end(run_id)

    task = asyncio.create_task(producer())
    seen: list = []
    async for item in bridge.subscribe(run_id, 0):
        seen.append(item)
    await task

    assert seen[0] == {"type": "a"}
    assert seen[1] == {"type": "b"}
    assert seen[2] is END_SENTINEL


@pytest.mark.asyncio
async def test_subscribe_timeout_emits_heartbeat() -> None:
    bridge = MemoryStreamBridge()
    run_id = "run-hb"

    async def slow_producer() -> None:
        await asyncio.sleep(0.08)
        await bridge.publish(run_id, {"type": "finish"})
        await bridge.publish_end(run_id)

    task = asyncio.create_task(slow_producer())
    seen: list = []
    async for item in bridge.subscribe(run_id, 0.03):
        seen.append(item)
    await task

    assert any(x is HEARTBEAT_SENTINEL for x in seen)
    assert any(isinstance(x, dict) and x.get("type") == "finish" for x in seen)


@pytest.mark.asyncio
async def test_publish_error_propagates_to_subscriber() -> None:
    bridge = MemoryStreamBridge()
    run_id = "run-err"

    async def failing_producer() -> None:
        await bridge.publish_error(run_id, ValueError("boom"))
        await bridge.publish_end(run_id)

    task = asyncio.create_task(failing_producer())
    seen: list = []
    async for item in bridge.subscribe(run_id, 0):
        seen.append(item)
    await task

    assert len(seen) == 1
    assert isinstance(seen[0], StreamBridgeError)
    assert str(seen[0].exc) == "boom"


@pytest.mark.asyncio
async def test_cleanup_removes_queue() -> None:
    bridge = MemoryStreamBridge()
    run_id = "run-clean"
    await bridge.publish(run_id, {"x": 1})
    bridge.cleanup(run_id)
    assert run_id not in bridge._queues


@pytest.mark.asyncio
async def test_iter_bridge_events_cancels_producer_on_exit() -> None:
    bridge = MemoryStreamBridge()
    run_id = "run-cancel"
    producer_started = asyncio.Event()
    producer_cancelled = asyncio.Event()

    async def gen():
        producer_started.set()
        try:
            await asyncio.sleep(10)
            yield {"type": "never"}
        except asyncio.CancelledError:
            producer_cancelled.set()
            raise

    async def consume() -> list:
        seen: list = []
        async for ev in iter_bridge_events(
            bridge, run_id, gen(), keepalive_seconds=0
        ):
            seen.append(ev)
            if ev is END_SENTINEL:
                break
        return seen

    task = asyncio.create_task(consume())
    await producer_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.05)
    assert producer_cancelled.is_set()


@pytest.mark.asyncio
async def test_run_stream_producer_publishes_generator_items() -> None:
    bridge = MemoryStreamBridge()
    run_id = "run-prod"

    async def gen():
        yield {"n": 1}
        yield {"n": 2}

    await run_stream_producer(gen(), bridge, run_id)
    seen: list = []
    async for item in bridge.subscribe(run_id, 0):
        seen.append(item)

    assert seen[:2] == [{"n": 1}, {"n": 2}]
    assert seen[2] is END_SENTINEL
