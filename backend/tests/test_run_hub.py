"""RunHub 契约（enable-distributed-sse-pubsub task 4.3）。

共享订阅 / subscribe-first 握手去重 / gap snapshot 对账 / 多 Tab fan-out /
单 Tab 超限只断开自己 / 旧 term 过滤 / 末 Tab 释放。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from noesis.chat.runs import hub as hub_module
from noesis.chat.runs.bus import InMemoryRunBus, RunEventEnvelope
from noesis.chat.runs.hub import RunHubRegistry


class _Snap:
    def __init__(self, sequence: int = 0, owner_term: int = 1, terminal: bool = False):
        self.sequence = sequence
        self.owner_term = owner_term
        self.is_terminal = terminal

    def to_dict(self):
        return {"type": "run-snapshot", "sequence": self.sequence, "status": "running"}


def _env(sequence: int, *, owner_term: int = 1, event_type: str = "text-delta"):
    return RunEventEnvelope(
        schema_version=1,
        run_id="run-1",
        owner_instance_id="instance-1",
        owner_term=owner_term,
        sequence=sequence,
        attempt_id=1,
        event_type=event_type,
        payload={"type": event_type, "delta": f"d{sequence}", "sequence": sequence},
    )


async def _publish(bus, envelopes):
    await bus.publish_run_events("run-1", envelopes)


def _registry(bus, snap: _Snap) -> RunHubRegistry:
    async def loader(run_id: str):
        return snap

    return RunHubRegistry(bus=bus, snapshot_loader=loader)


async def _get(queue, timeout: float = 2.0):
    return await asyncio.wait_for(queue.get(), timeout=timeout)


@pytest.mark.asyncio
async def test_handshake_snapshot_covers_prior_events() -> None:
    """订阅前发生的事件含在 snapshot（bus 端无人订阅本就收不到）。"""
    bus = InMemoryRunBus(envelope_payload_max_bytes=64 * 1024)
    registry = _registry(bus, _Snap(sequence=2))
    try:
        sub = await registry.subscribe("run-1")
        assert sub.snapshot.sequence == 2
        await _publish(bus, [_env(3)])
        item = await _get(sub.queue)
        assert item["sequence"] == 3
        await sub.close()
    finally:
        await registry.close_all()
        await bus.close()


@pytest.mark.asyncio
async def test_events_during_handshake_are_deduped() -> None:
    """握手窗口内（订阅 ack 之后、snapshot 读取之前）到达的事件不丢不重。"""
    bus = InMemoryRunBus(envelope_payload_max_bytes=64 * 1024)
    gate = asyncio.Event()

    async def loader(run_id: str):
        await gate.wait()
        return _Snap(sequence=1)

    registry = RunHubRegistry(bus=bus, snapshot_loader=loader)
    try:
        subscribing = asyncio.create_task(registry.subscribe("run-1"))
        # 等 bus 订阅建立（reader 就绪前发布的事件进入订阅 queue 缓冲）
        for _ in range(200):
            if bus._run_channels:
                break
            await asyncio.sleep(0.005)
        await _publish(bus, [_env(1), _env(2)])
        gate.set()
        sub = await subscribing
        # snapshot sequence=1：事件 1 已含在 snapshot 内，只投递 2
        item = await _get(sub.queue)
        assert item["sequence"] == 2
        await sub.close()
    finally:
        await registry.close_all()
        await bus.close()


@pytest.mark.asyncio
async def test_multi_tab_shares_single_hub_and_bus_subscription() -> None:
    bus = InMemoryRunBus(envelope_payload_max_bytes=64 * 1024)
    registry = _registry(bus, _Snap(sequence=0))
    try:
        sub_a = await registry.subscribe("run-1")
        sub_b = await registry.subscribe("run-1")
        assert registry.hub_count == 1, "同 Run 多 Tab 共享一个 hub"
        await _publish(bus, [_env(1)])
        assert (await _get(sub_a.queue))["sequence"] == 1
        assert (await _get(sub_b.queue))["sequence"] == 1
        await sub_a.close()
        await sub_b.close()
    finally:
        await registry.close_all()
        await bus.close()


@pytest.mark.asyncio
async def test_gap_triggers_snapshot_resync() -> None:
    """sequence 跳号：重读 snapshot，向 Tab 广播置换帧，后续事件续投。"""
    bus = InMemoryRunBus(envelope_payload_max_bytes=64 * 1024)
    # 握手时 snapshot 落后（sequence=0）；gap 触发的对账读取追到 sequence=3
    calls = {"n": 0}

    async def loader(run_id: str):
        calls["n"] += 1
        return _Snap(sequence=0) if calls["n"] == 1 else _Snap(sequence=3)

    registry = RunHubRegistry(bus=bus, snapshot_loader=loader)
    try:
        sub = await registry.subscribe("run-1")
        await _publish(bus, [_env(1)])  # 1 正常投递
        assert (await _get(sub.queue))["sequence"] == 1
        await _publish(bus, [_env(3)])  # 跳过 2 → gap
        replacement = await _get(sub.queue)
        assert replacement["type"] == "run-snapshot", "gap 后广播置换帧"
        assert replacement["sequence"] == 3
        await _publish(bus, [_env(4)])
        item = await _get(sub.queue)
        assert item["sequence"] == 4
        await sub.close()
    finally:
        await registry.close_all()
        await bus.close()


@pytest.mark.asyncio
async def test_tab_overflow_closes_only_that_tab(monkeypatch) -> None:
    bus = InMemoryRunBus(envelope_payload_max_bytes=64 * 1024)
    registry = _registry(bus, _Snap(sequence=0))
    try:
        monkeypatch.setattr(hub_module, "_TAB_QUEUE_MAX", 2)
        sub_a = await registry.subscribe("run-1")
        monkeypatch.setattr(hub_module, "_TAB_QUEUE_MAX", 512)
        sub_b = await registry.subscribe("run-1")
        received_b: list = []

        async def _consume_b():
            while True:
                received_b.append(await sub_b.queue.get())

        consumer = asyncio.create_task(_consume_b())
        # A 不消费：queue 上限 2，第 3 条起溢出 → A 收哨兵；B 持续消费不受影响
        await _publish(bus, [_env(i) for i in range(1, 6)])
        # A 的流在残余缓冲事件后以哨兵终止（客户端重连后经 snapshot 重建）
        items_a = []
        while True:
            item = await _get(sub_a.queue)
            if item is None:
                break
            items_a.append(item)
        assert items_a, "溢出前缓冲的事件仍可读"
        assert items_a[-1]["sequence"] <= 2, "A 不应收到溢出后的事件"
        for _ in range(100):
            if len(received_b) >= 5:
                break
            await asyncio.sleep(0.005)
        assert [i["sequence"] for i in received_b] == [1, 2, 3, 4, 5], "其余 Tab 不受影响"
        consumer.cancel()
        await sub_a.close()
        await sub_b.close()
    finally:
        await registry.close_all()
        await bus.close()


@pytest.mark.asyncio
async def test_stale_term_envelope_ignored() -> None:
    bus = InMemoryRunBus(envelope_payload_max_bytes=64 * 1024)
    registry = _registry(bus, _Snap(sequence=0, owner_term=2))
    try:
        sub = await registry.subscribe("run-1")
        await _publish(bus, [_env(1, owner_term=1)])  # 旧 term
        with pytest.raises(asyncio.TimeoutError):
            await _get(sub.queue, timeout=0.2)
        await _publish(bus, [_env(1, owner_term=2)])
        assert (await _get(sub.queue))["sequence"] == 1
        await sub.close()
    finally:
        await registry.close_all()
        await bus.close()


@pytest.mark.asyncio
async def test_last_tab_releases_hub_and_bus_channel() -> None:
    bus = InMemoryRunBus(envelope_payload_max_bytes=64 * 1024)
    registry = _registry(bus, _Snap(sequence=0))
    try:
        sub = await registry.subscribe("run-1")
        await sub.close()
        await sub.close()  # 幂等
        for _ in range(100):
            if registry.hub_count == 0:
                break
            await asyncio.sleep(0.005)
        assert registry.hub_count == 0, "最后一个 Tab 离开应释放 hub"
        assert not bus._run_channels, "bus channel 应已退订"
    finally:
        await registry.close_all()
        await bus.close()


@pytest.mark.asyncio
async def test_periodic_reconciliation_converges_after_silent_loss() -> None:
    """周期对账（task 4.5）：静默期丢事件（含终态）在周期内收敛。"""
    bus = InMemoryRunBus(envelope_payload_max_bytes=64 * 1024)
    state = {"sequence": 0}

    async def loader(run_id: str):
        return _Snap(sequence=state["sequence"])

    registry = RunHubRegistry(
        bus=bus, snapshot_loader=loader, reconciliation_interval_seconds=0.05
    )
    try:
        sub = await registry.subscribe("run-1")
        await _publish(bus, [_env(1)])
        assert (await _get(sub.queue))["sequence"] == 1
        # 模拟静默丢失：DB 已推进到 3，但 bus 上没有后续事件
        state["sequence"] = 3
        replacement = await _get(sub.queue, timeout=2)
        assert replacement["type"] == "run-snapshot"
        assert replacement["sequence"] == 3
        await sub.close()
    finally:
        await registry.close_all()
        await bus.close()


@pytest.mark.asyncio
async def test_late_lower_sequence_event_dropped() -> None:
    """乱序（迟到的小 sequence 事件）：对账后到达的旧事件丢弃，不回退。"""
    bus = InMemoryRunBus(envelope_payload_max_bytes=64 * 1024)
    registry = _registry(bus, _Snap(sequence=2))
    try:
        sub = await registry.subscribe("run-1")
        # 对账已到 2；迟到的 sequence 1（乱序/重放）与 sequence 2 均为旧事件
        await _publish(bus, [_env(1), _env(2), _env(3)])
        item = await _get(sub.queue)
        assert item["sequence"] == 3, "只投递对齐后的新事件"
        with pytest.raises(asyncio.TimeoutError):
            await _get(sub.queue, timeout=0.2)
        await sub.close()
    finally:
        await registry.close_all()
        await bus.close()
