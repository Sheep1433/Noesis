"""Run bus 契约测试：memory adapter（P4 起参数化加入 redis adapter，同一组用例）。

对应 openspec enable-distributed-sse-pubsub task 1.3：Service 层只见 port；
两个 adapter 必须共享同一版本化 envelope 与语义（at-most-once、subscribe-first
ack、channel 隔离、close 幂等）。
"""

from __future__ import annotations

import asyncio

import pytest

from noesis.chat.runs.bus import (
    RUN_BUS_SCHEMA_VERSION,
    EnvelopePayloadTooLarge,
    InMemoryRunBus,
    RunEventEnvelope,
)


def _envelope(
    run_id: str = "run-1",
    sequence: int = 1,
    event_type: str = "text-delta",
    payload: dict | None = None,
) -> RunEventEnvelope:
    return RunEventEnvelope(
        schema_version=RUN_BUS_SCHEMA_VERSION,
        run_id=run_id,
        owner_instance_id="instance-a",
        owner_term=1,
        sequence=sequence,
        attempt_id=1,
        event_type=event_type,
        payload=payload or {"delta": "x"},
    )


@pytest.fixture(params=["memory"])
def bus(request) -> InMemoryRunBus:
    if request.param == "memory":
        return InMemoryRunBus(envelope_payload_max_bytes=64 * 1024)
    raise NotImplementedError("redis adapter 在 P4 接入本参数化")


@pytest.mark.asyncio
async def test_envelope_roundtrip_preserves_fields(bus) -> None:
    envelope = _envelope(payload={"delta": "你好", "n": 3})
    data = envelope.to_dict()
    restored = RunEventEnvelope.from_dict(data)
    assert restored == envelope


@pytest.mark.asyncio
async def test_unknown_schema_version_rejected(bus) -> None:
    with pytest.raises(ValueError, match="schema_version"):
        RunEventEnvelope.from_dict({"schema_version": 99, "run_id": "r"})
    with pytest.raises(ValueError, match="schema_version"):
        await bus.publish_run_events(
            "run-1",
            [
                RunEventEnvelope(
                    schema_version=99,
                    run_id="run-1",
                    owner_instance_id="a",
                    owner_term=1,
                    sequence=1,
                    attempt_id=1,
                    event_type="text-delta",
                    payload={},
                )
            ],
        )


@pytest.mark.asyncio
async def test_payload_over_limit_rejected(bus) -> None:
    small_bus = InMemoryRunBus(envelope_payload_max_bytes=8)
    with pytest.raises(EnvelopePayloadTooLarge):
        await small_bus.publish_run_events("run-1", [_envelope(payload={"delta": "x" * 64})])


@pytest.mark.asyncio
async def test_subscribe_first_ack_then_publish_visible(bus) -> None:
    subscription = await bus.subscribe_run_events("run-1")
    await subscription.ready()
    await bus.publish_run_events("run-1", [_envelope(sequence=1), _envelope(sequence=2)])

    iterator = subscription.__aiter__().__anext__()
    first = await asyncio.wait_for(iterator, timeout=1)
    assert first.sequence == 1
    second = await asyncio.wait_for(subscription.__aiter__().__anext__(), timeout=1)
    assert second.sequence == 2
    await subscription.close()


@pytest.mark.asyncio
async def test_run_channel_isolation(bus) -> None:
    sub_a = await bus.subscribe_run_events("run-a")
    sub_b = await bus.subscribe_run_events("run-b")
    await bus.publish_run_events("run-a", [_envelope(run_id="run-a")])

    item = await asyncio.wait_for(sub_a.__aiter__().__anext__(), timeout=1)
    assert item.run_id == "run-a"
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(sub_b.__aiter__().__anext__(), timeout=0.1)
    await sub_a.close()
    await sub_b.close()


@pytest.mark.asyncio
async def test_close_is_idempotent_and_releases_channel(bus) -> None:
    subscription = await bus.subscribe_run_events("run-1")
    await subscription.close()
    await subscription.close()  # 幂等
    # 释放后 channel 无订阅者残留
    assert not bus._run_channels


@pytest.mark.asyncio
async def test_wakeup_subscribe_first_delivery(bus) -> None:
    wakeup = bus.subscribe_wakeups()
    await wakeup.ready()
    await bus.wakeup("run-created", {"run_id": "run-9"})

    message = await asyncio.wait_for(wakeup.__aiter__().__anext__(), timeout=1)
    assert message.topic == "run-created"
    assert message.payload["run_id"] == "run-9"
    await wakeup.close()


@pytest.mark.asyncio
async def test_wakeup_closed_subscription_stops_iterating(bus) -> None:
    wakeup = bus.subscribe_wakeups()
    await wakeup.close()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(wakeup.__aiter__().__anext__(), timeout=1)


@pytest.mark.asyncio
async def test_bus_close_ends_subscriptions(bus) -> None:
    subscription = await bus.subscribe_run_events("run-1")
    await bus.close()
    with pytest.raises((StopAsyncIteration, asyncio.TimeoutError)):
        await asyncio.wait_for(subscription.__aiter__().__anext__(), timeout=1)
