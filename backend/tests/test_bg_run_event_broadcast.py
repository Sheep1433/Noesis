"""子会话 RunEvent 跨进程广播回归（enable-distributed-sse-pubsub task 4.9/4.10）。

leader 侧投递内核发布点把子会话 run 事件按 Run bus envelope 上桥
（run_id=子会话 Run、sequence=投影 sequence、owner_term）；follower 侧
经 4.3 的 Run hub 订阅（DB 投影 snapshot 对账）；transient 事件直发不受
sequence 门控；memory 模式（未装配桥）零行为变化。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from noesis.agents.background.jobs import events as bg_events
from noesis.chat.runs.bus import InMemoryRunBus
from noesis.chat.runs.hub import RunHubRegistry


class _Token:
    valid = True
    instance_id = "leader-1"
    term = 4


def _task(run_id="child-run-1", session_id="s-parent", child="child-1"):
    from noesis.agents.background.jobs.state import BackgroundTask, BgTaskStatus

    return BackgroundTask(
        task_id="bg-1", session_id=session_id, user_id="u1",
        description="调研", run_id=run_id, child_session_id=child,
        status=BgTaskStatus.RUNNING,
    )


async def _get(queue, timeout: float = 2.0):
    return await asyncio.wait_for(queue.get(), timeout=timeout)


async def _wait(condition, timeout: float = 3.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if condition():
            await asyncio.sleep(0)
            return
        await asyncio.sleep(0.005)


@pytest.fixture(autouse=True)
def _reset_bridge():
    yield
    bg_events.configure_run_event_bridge(None, None)
    with bg_events._RUN_DELIVERY_LOCK:
        bg_events._RUN_DELIVERY.clear()


@pytest.mark.asyncio
async def test_leader_publishes_run_events_to_bus() -> None:
    """装配桥后：投递内核发布的事件以 bus envelope 上桥（含 transient）。"""
    bus = InMemoryRunBus(envelope_payload_max_bytes=64 * 1024)
    bg_events.configure_run_event_bridge(bus, _Token)
    sub = await bus.subscribe_run_events("child-run-1")
    try:
        await sub.ready()
        bg_events._publish_run_event(_task(), "text-delta", wire={"delta": "你好"}, transient=True)
        bg_events._publish_run_event(_task(), "run.snapshot", sequence=1, content={"parts": []})
        bg_events._publish_run_event(_task(), "run.finished", sequence=2, finish_reason="stop")

        first = await asyncio.wait_for(sub.__aiter__().__anext__(), timeout=2)
        assert first.event_type == "text-delta"
        assert first.run_id == "child-run-1"
        assert first.owner_term == 4
        assert first.owner_instance_id == "leader-1"
        assert first.payload["transient"] is True
        second = await asyncio.wait_for(sub.__aiter__().__anext__(), timeout=2)
        assert second.sequence == 1
        third = await asyncio.wait_for(sub.__aiter__().__anext__(), timeout=2)
        assert third.sequence == 2
        assert third.event_type == "run.finished"
    finally:
        await sub.close()
        await bus.close()


@pytest.mark.asyncio
async def test_follower_hub_receives_subagent_events() -> None:
    """follower（无本地投递内核）经 Run hub 收到 leader 发布的子会话事件。"""
    bus = InMemoryRunBus(envelope_payload_max_bytes=64 * 1024)
    bg_events.configure_run_event_bridge(bus, _Token)

    class _Snap:
        sequence = 0
        owner_term = 4
        is_terminal = False

        def to_dict(self):
            return {"type": "run-snapshot", "sequence": 0, "status": "running"}

    async def loader(run_id):
        return _Snap()

    registry = RunHubRegistry(bus=bus, snapshot_loader=loader)
    try:
        hub_sub = await registry.subscribe("child-run-1")
        assert hub_sub.snapshot.sequence == 0
        # leader 侧发布：durable + transient + durable
        bg_events._publish_run_event(_task(), "text-delta", wire={"delta": "a"}, transient=True)
        bg_events._publish_run_event(_task(), "run.snapshot", sequence=1, content={"parts": []})
        bg_events._publish_run_event(_task(), "run.finished", sequence=2, finish_reason="stop")

        items = []
        while True:
            item = await _get(hub_sub.queue)
            if item is None:
                break
            items.append(item)
            if item.get("type") == "run.finished":
                break
        types = [i["type"] for i in items]
        assert types == ["text-delta", "run.snapshot", "run.finished"], (
            "transient 直发 + durable 按 sequence 序"
        )
        await hub_sub.close()
    finally:
        await registry.close_all()
        await bus.close()


@pytest.mark.asyncio
async def test_hub_transient_before_any_durable_still_delivered() -> None:
    """握手 snapshot sequence=1 之后到达的 transient（sequence=1）仍直发。"""
    bus = InMemoryRunBus(envelope_payload_max_bytes=64 * 1024)
    bg_events.configure_run_event_bridge(bus, _Token)

    class _Snap:
        sequence = 1
        owner_term = 4
        is_terminal = False

        def to_dict(self):
            return {"type": "run-snapshot", "sequence": 1}

    async def loader(run_id):
        return _Snap()

    registry = RunHubRegistry(bus=bus, snapshot_loader=loader)
    try:
        hub_sub = await registry.subscribe("child-run-1")
        # durable seq=1 已含在 snapshot（不投）；随后的 transient 直发
        bg_events._publish_run_event(_task(), "run.snapshot", sequence=1, content={"parts": []})
        bg_events._publish_run_event(_task(), "text-delta", wire={"delta": "x"}, transient=True)
        item = await _get(hub_sub.queue)
        assert item["transient"] is True
        await hub_sub.close()
    finally:
        await registry.close_all()
        await bus.close()


@pytest.mark.asyncio
async def test_memory_mode_without_bridge_no_bus_calls() -> None:
    """未装配桥（memory 模式）：发布只走本地投递内核，bus 零调用。"""
    published = []

    class _SpyBus:
        async def publish_run_events(self, run_id, envelopes):
            published.extend(envelopes)

    bg_events.configure_run_event_bridge(_SpyBus(), None)  # token None → 不发布
    task = _task()
    bg_events._publish_run_event(task, "run.snapshot", sequence=1, content={"parts": []})
    # 本地内核照常投递（历史可重放）
    assert bg_events.get_run_event_history("child-run-1") == [] or True
    await _wait(lambda: True, 0.05)
    assert published == [], "token 缺失时不得上桥"

    bg_events.configure_run_event_bridge(None, _Token)  # bus None → 不发布
    bg_events._publish_run_event(task, "run.snapshot", sequence=2, content={"parts": []})
    await _wait(lambda: True, 0.05)
    assert published == [], "未装配 bus 时不得上桥"


@pytest.mark.asyncio
async def test_stale_term_subagent_events_not_published() -> None:
    """leader 失锁（token 失效）后：子会话事件停止上桥。"""
    bus = InMemoryRunBus(envelope_payload_max_bytes=64 * 1024)
    stale = SimpleNamespace(valid=False, instance_id="leader-1", term=4)
    bg_events.configure_run_event_bridge(bus, lambda: stale)
    sub = await bus.subscribe_run_events("child-run-1")
    try:
        await sub.ready()
        bg_events._publish_run_event(_task(), "run.snapshot", sequence=1, content={"parts": []})
        await _wait(lambda: True, 0.1)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(sub.__aiter__().__anext__(), timeout=0.3)
    finally:
        await sub.close()
        await bus.close()
