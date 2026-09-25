"""RunEventPublisher 契约（worker-role-split：owner/epoch 构造期固定）。

单 consumer 有界 outbound queue：sequence 严格有序、终态经同一 queue 不
越位、overflow/publish 失败丢弃不阻塞；envelope.owner_term 携带 claim
epoch（语义自 leader term 迁移，hub 迟到过滤机制不变）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from noesis.chat.runs.bus import RUN_BUS_SCHEMA_VERSION
from noesis.chat.runs.manager import RunManager, SequencedRunEvent
from noesis.chat.runs.bus import InMemoryRunBus
from noesis.chat.runs.publisher import RunEventPublisher
from noesis.chat.delivery.events import RunCompleted, WireFrame


class _RecordingBus:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[Any] = []
        self.fail = fail

    async def publish_run_events(self, run_id: str, envelopes) -> None:
        if self.fail:
            raise RuntimeError("bus down")
        self.calls.extend(envelopes)


def _event(sequence: int, event: Any = None) -> SequencedRunEvent:
    if event is None:
        event = WireFrame(event="text-delta", data={"delta": f"chunk-{sequence}"})
    return SequencedRunEvent(run_id="run-1", sequence=sequence, attempt_id=1, event=event)


async def _drain(publisher: RunEventPublisher) -> None:
    """等待 consumer 清空队列（事件级同步，替代轮询）。"""
    for _ in range(200):
        if publisher._queue.empty():
            await asyncio.sleep(0)
            if publisher._queue.empty():
                return
        await asyncio.sleep(0.005)


def _publisher(bus, **overrides) -> RunEventPublisher:
    kwargs = dict(
        run_id="run-1",
        bus=bus,
        owner_instance_id="worker-1",
        claim_epoch=3,
        max_events=16,
        max_bytes=64 * 1024,
    )
    kwargs.update(overrides)
    return RunEventPublisher(**kwargs)


@pytest.mark.asyncio
async def test_publishes_in_sequence_order_with_epoch_envelope() -> None:
    bus = _RecordingBus()
    publisher = _publisher(bus)
    publisher.start()
    try:
        for sequence in (1, 2, 3):
            publisher.submit(_event(sequence))
        await _drain(publisher)
    finally:
        await publisher.stop()

    assert [e.sequence for e in bus.calls] == [1, 2, 3]
    first = bus.calls[0]
    assert first.schema_version == RUN_BUS_SCHEMA_VERSION
    assert first.owner_instance_id == "worker-1"
    # 语义迁移：owner_term 携带 claim epoch（hub 迟到过滤按 epoch 兼容工作）
    assert first.owner_term == 3
    assert first.event_type == "text-delta"
    # wire 载荷与 SSE 同源：事件字段 + run_id/sequence/attempt_id 注入
    assert first.payload["delta"] == "chunk-1"
    assert first.payload["run_id"] == "run-1"
    assert first.payload["sequence"] == 1
    assert first.payload["attempt_id"] == 1


@pytest.mark.asyncio
async def test_terminal_goes_through_same_queue_after_regular_events() -> None:
    """终态经同一 queue：不越过尚未发布的普通事件（发布序 = sequence 序）。"""
    bus = _RecordingBus()
    publisher = _publisher(bus)
    publisher.start()
    try:
        slow = asyncio.Event()

        original_publish = bus.publish_run_events

        async def _slow_publish(run_id, envelopes):
            await slow.wait()
            await original_publish(run_id, envelopes)

        bus.publish_run_events = _slow_publish  # type: ignore[method-assign]
        publisher.submit(_event(1))
        publisher.submit(_event(2))
        publisher.submit(_event(3, event=RunCompleted()))
        await asyncio.sleep(0.02)  # consumer 阻塞在第一条
        slow.set()
        await _drain(publisher)
    finally:
        await publisher.stop()

    assert [e.sequence for e in bus.calls] == [1, 2, 3]


@pytest.mark.asyncio
async def test_overflow_drops_without_blocking() -> None:
    bus = _RecordingBus()
    metrics: list[str] = []
    publisher = _publisher(bus, max_events=2, max_bytes=1024, on_metric=metrics.append)
    publisher.start()
    # 不让 consumer 消费：先堵住 bus
    gate = asyncio.Event()
    original = bus.publish_run_events

    async def _blocked(run_id, envelopes):
        await gate.wait()
        await original(run_id, envelopes)

    bus.publish_run_events = _blocked  # type: ignore[method-assign]
    try:
        for sequence in range(1, 6):
            publisher.submit(_event(sequence))
        assert publisher.dropped_overflow == 3, "超出 max_events=2 的事件应丢弃"
        assert "bus_publisher_overflow" in metrics
    finally:
        gate.set()
        await publisher.stop()


@pytest.mark.asyncio
async def test_publish_failure_does_not_stop_consumer() -> None:
    bus = _RecordingBus(fail=True)
    metrics: list[str] = []
    publisher = _publisher(bus, max_events=8, max_bytes=1024, on_metric=metrics.append)
    publisher.start()
    try:
        publisher.submit(_event(1))
        publisher.submit(_event(2))
        await _drain(publisher)
    finally:
        await publisher.stop()
    assert publisher.publish_failures == 2, "失败不重试、不中断消费"
    assert metrics.count("bus_publisher_failures") == 2


@pytest.mark.asyncio
async def test_manager_publishes_to_bus_with_claim_context() -> None:
    """集成：装配 bus 且 start 带认领上下文时，扇出同步广播 epoch 信封。"""
    release = asyncio.Event()
    completed = asyncio.Event()

    async def producer(publish):
        await publish(WireFrame(event="text-delta", data={"delta": "hello"}))
        await release.wait()
        completed.set()

    bus = InMemoryRunBus(envelope_payload_max_bytes=64 * 1024)
    manager = RunManager()
    manager.attach_bus(bus)
    subscription = await bus.subscribe_run_events("run-1")
    try:
        handle = await manager.start(
            run_id="run-1",
            session_id="session-1",
            user_id="user-1",
            assistant_message_id="message-1",
            snapshot_provider=_snapshot_stub(),
            producer=producer,
            owner_instance_id="worker-9",
            claim_epoch=7,
        )
        first = await asyncio.wait_for(subscription.__aiter__().__anext__(), timeout=2)
        assert first.payload["delta"] == "hello"
        assert first.owner_instance_id == "worker-9"
        assert first.owner_term == 7
        release.set()
        await asyncio.wait_for(completed.wait(), timeout=2)
        await handle.producer_task
    finally:
        await subscription.close()
        await manager.shutdown(drain_seconds=0)
        await bus.close()


@pytest.mark.asyncio
async def test_manager_without_claim_context_skips_publisher() -> None:
    """start 未带认领上下文（channel 等非认领路径）：不建 publisher，行为不变。"""
    release = asyncio.Event()

    async def producer(publish):
        await publish(WireFrame(event="text-delta", data={"delta": "x"}))
        await release.wait()

    manager = RunManager()
    manager.attach_bus(object())  # bus 装配但无认领上下文
    handle = await manager.start(
        run_id="run-2",
        session_id="s",
        user_id="u",
        assistant_message_id="m",
        snapshot_provider=_snapshot_stub(),
        producer=producer,
    )
    assert handle.publisher is None
    release.set()
    await handle.producer_task
    await manager.shutdown(drain_seconds=0)


def _snapshot_stub():
    from noesis.chat.runs.manager import RunSnapshot, RunStatus

    def _provider(sequence: int, status, attempt_id: int):
        return RunSnapshot(
            run_id="run-1", sequence=sequence, status=RunStatus.RUNNING,
            attempt_id=attempt_id,
        )

    return _provider
