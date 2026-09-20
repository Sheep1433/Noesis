"""Run bus 契约测试：memory / redis adapter 同一组用例参数化。

对应 openspec enable-distributed-sse-pubsub task 1.3/4.1：Service 层只见
port；两个 adapter 必须共享同一版本化 envelope 与语义（at-most-once、
subscribe-first ack、channel 隔离、close 幂等）。redis 用例需要可用的
Redis（``TEST_REDIS_URL``，默认本机 6379；不可达时 skip——CI 提供真实
Redis service，memory 用例不依赖）。
"""

from __future__ import annotations

import asyncio
import os

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


@pytest.fixture(params=["memory", "redis"])
async def bus(request):
    if request.param == "memory":
        instance = InMemoryRunBus(envelope_payload_max_bytes=64 * 1024)
        yield instance
        await instance.close()
        return

    import redis.asyncio as aioredis

    from noesis.chat.runs.bus_redis import RedisRunBus

    client = aioredis.from_url(
        os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
        socket_timeout=2,
        socket_connect_timeout=1,
    )
    try:
        await asyncio.wait_for(client.ping(), timeout=1.5)
    except Exception:
        await client.aclose()
        pytest.skip("Redis 不可达（TEST_REDIS_URL 可覆盖；CI 提供真实 Redis service）")
    # 每 worker 唯一 cluster：与同库其他测试运行的 channel 严格隔离
    cluster = f"contract-{os.getpid()}"
    instance = RedisRunBus(
        client=client, cluster_id=cluster, envelope_payload_max_bytes=64 * 1024
    )
    yield instance
    await instance.close()


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
async def test_payload_over_limit_rejected(bus, request) -> None:
    if request.node.callspec.params["bus"] == "memory":
        small_bus = InMemoryRunBus(envelope_payload_max_bytes=8)
    else:
        import redis.asyncio as aioredis

        from noesis.chat.runs.bus_redis import RedisRunBus

        client = aioredis.from_url(
            os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
        small_bus = RedisRunBus(client=client, cluster_id="contract-small", envelope_payload_max_bytes=8)
    try:
        with pytest.raises(EnvelopePayloadTooLarge):
            await small_bus.publish_run_events("run-1", [_envelope(payload={"delta": "x" * 64})])
    finally:
        await small_bus.close()


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
    # 释放后 channel 无订阅者残留（两 adapter 的本地注册表同构清空）
    channels = getattr(bus, "_run_channels", None)
    if channels is None:
        channels = bus._run_queues
    assert not channels


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


# ---------------------------------------------------------------------------
# redis 专属：环境隔离与断连重订阅（task 4.1）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_redis_cluster_id_isolates_channels() -> None:
    """不同 cluster_id 的同名 run channel 互不可见（环境隔离）。"""
    import redis.asyncio as aioredis

    from noesis.chat.runs.bus_redis import RedisRunBus

    url = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/0")
    client = aioredis.from_url(url, decode_responses=True)
    try:
        await asyncio.wait_for(client.ping(), timeout=1.5)
    except Exception:
        await client.aclose()
        pytest.skip("Redis 不可达")

    bus_a = RedisRunBus(client=aioredis.from_url(url, decode_responses=True), cluster_id="cluster-a", envelope_payload_max_bytes=64 * 1024)
    bus_b = RedisRunBus(client=aioredis.from_url(url, decode_responses=True), cluster_id="cluster-b", envelope_payload_max_bytes=64 * 1024)
    try:
        sub_a = await bus_a.subscribe_run_events("run-1")
        sub_b = await bus_b.subscribe_run_events("run-1")
        await bus_a.publish_run_events("run-1", [_envelope(run_id="run-1")])
        item = await asyncio.wait_for(sub_a.__aiter__().__anext__(), timeout=2)
        assert item.run_id == "run-1"
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(sub_b.__aiter__().__anext__(), timeout=0.3)
        await sub_a.close()
        await sub_b.close()
    finally:
        await bus_a.close()
        await bus_b.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_redis_reconnect_resubscribes_and_continues() -> None:
    """服务端踢掉 pubsub 连接后：reader 退避重试，重订阅恢复投递（at-most-once 窗口由上层兜底）。"""
    import redis.asyncio as aioredis

    from noesis.chat.runs.bus_redis import RedisRunBus

    url = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/0")
    admin = aioredis.from_url(url, decode_responses=True)
    try:
        await asyncio.wait_for(admin.ping(), timeout=1.5)
    except Exception:
        await admin.aclose()
        pytest.skip("Redis 不可达")

    bus = RedisRunBus(
        client=aioredis.from_url(url, decode_responses=True),
        cluster_id=f"reconnect-{os.getpid()}",
        envelope_payload_max_bytes=64 * 1024,
    )
    try:
        sub = await bus.subscribe_run_events("run-1")
        # 服务端断开全部 pubsub 连接（专用测试容器，无第三方受害者）
        await admin.execute_command("CLIENT", "KILL", "TYPE", "PUBSUB")
        await asyncio.sleep(1.0)  # 覆盖 reader 退避窗口，等待重连重订阅
        await bus.publish_run_events("run-1", [_envelope(run_id="run-1", sequence=7)])
        item = await asyncio.wait_for(sub.__aiter__().__anext__(), timeout=5)
        assert item.sequence == 7
        await sub.close()
    finally:
        await bus.close()
        await admin.aclose()


@pytest.mark.asyncio
async def test_signal_publish_subscribe_roundtrip(bus) -> None:
    """信令通道（task 4.7）：版本化 envelope、无 sequence、scope 隔离。"""
    sub = await bus.subscribe_signals("user", "user-1")
    await sub.ready()
    await bus.publish_signal("user", "user-1", {"type": "list-changed"}, origin="w1")
    message = await asyncio.wait_for(sub.__aiter__().__anext__(), timeout=1)
    assert message["schema_version"] == 1
    assert message["scope"] == "user"
    assert message["key"] == "user-1"
    assert message["origin"] == "w1"
    assert message["payload"]["type"] == "list-changed"
    assert "sequence" not in message
    # scope 隔离：别的通道收不到
    other = await bus.subscribe_signals("session", "user-1")
    await other.ready()
    await bus.publish_signal("user", "user-1", {"type": "again"}, origin="w1")
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(other.__aiter__().__anext__(), timeout=0.3)
    await sub.close()
    await other.close()
