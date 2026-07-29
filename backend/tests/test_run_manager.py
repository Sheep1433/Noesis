from __future__ import annotations

import asyncio

import pytest

from noesis_server.domain.chat.runs import (
    HitlPendingExpired,
    RunCapacityExceeded,
    RunDurationExceeded,
    RunManager,
    RunOutputExceeded,
    RunSnapshot,
    RunStatus,
    SequencedRunEvent,
    SlowSubscriber,
    StaleAttemptEvent,
)


def _snapshot(run_id: str = "run-1"):
    def build(sequence: int, status: RunStatus, attempt_id: int) -> RunSnapshot:
        return RunSnapshot(
            run_id=run_id,
            user_id="user-1",
            session_id="session-1",
            assistant_message_id="message-1",
            qa_type="COMMON_QA",
            origin="web",
            status=status,
            sequence=sequence,
            attempt_id=attempt_id,
        )

    return build


@pytest.mark.asyncio
async def test_unsubscribe_does_not_cancel_producer() -> None:
    release = asyncio.Event()
    completed = asyncio.Event()

    async def producer(publish):
        await publish({"type": "text-delta", "delta": "a"})
        await release.wait()
        await publish({"type": "finish"})
        completed.set()

    manager = RunManager()
    handle = await manager.start(
        run_id="run-1",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="message-1",
        snapshot_provider=_snapshot(),
        producer=producer,
    )
    subscription = await manager.subscribe("run-1")
    await manager.unsubscribe("run-1", subscription.queue)
    release.set()
    await asyncio.wait_for(completed.wait(), timeout=1)
    await handle.producer_task
    assert not handle.producer_task.cancelled()


@pytest.mark.asyncio
async def test_sequence_and_buffer_replay_are_continuous() -> None:
    release = asyncio.Event()

    async def producer(publish):
        await publish("one")
        await publish("two")
        await release.wait()

    manager = RunManager()
    handle = await manager.start(
        run_id="run-1",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="message-1",
        snapshot_provider=_snapshot(),
        producer=producer,
    )
    await asyncio.sleep(0)
    subscription = await manager.subscribe("run-1", after_sequence=1)
    assert subscription.snapshot.sequence == 2
    assert [item.sequence for item in subscription.replay] == [2]
    release.set()
    await handle.producer_task


@pytest.mark.asyncio
async def test_slow_subscriber_isolated_from_producer() -> None:
    release = asyncio.Event()

    async def producer(publish):
        await publish("one")
        await publish("two")
        release.set()

    manager = RunManager(subscriber_queue_events=1)
    handle = await manager.start(
        run_id="run-1",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="message-1",
        snapshot_provider=_snapshot(),
        producer=producer,
    )
    subscription = await manager.subscribe("run-1")
    await asyncio.wait_for(release.wait(), timeout=1)
    await handle.producer_task
    item = await subscription.queue.get()
    assert isinstance(item, SlowSubscriber)


@pytest.mark.asyncio
async def test_subscriber_byte_limit_isolated_from_producer() -> None:
    published = asyncio.Event()

    async def producer(publish):
        await publish("x" * 2_000)
        published.set()

    manager = RunManager(subscriber_queue_events=10, subscriber_queue_bytes=256)
    handle = await manager.start(
        run_id="run-1",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="message-1",
        snapshot_provider=_snapshot(),
        producer=producer,
    )
    subscription = await manager.subscribe("run-1")
    await asyncio.wait_for(published.wait(), timeout=1)
    await handle.producer_task
    assert isinstance(await subscription.queue.get(), SlowSubscriber)


@pytest.mark.asyncio
async def test_terminal_run_can_be_removed_without_losing_persisted_contract() -> None:
    async def producer(publish):
        await publish({"type": "finish"})

    manager = RunManager()
    handle = await manager.start(
        run_id="run-1",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="message-1",
        snapshot_provider=_snapshot(),
        producer=producer,
    )
    await handle.producer_task
    await manager.transition("run-1", RunStatus.COMPLETED)
    assert await manager.remove_terminal("run-1")


@pytest.mark.asyncio
async def test_user_active_run_limit() -> None:
    release = asyncio.Event()

    async def producer(publish):
        await release.wait()

    manager = RunManager(max_user_active_runs=1)
    first = await manager.start(
        run_id="run-1",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="message-1",
        snapshot_provider=_snapshot("run-1"),
        producer=producer,
    )
    with pytest.raises(RunCapacityExceeded):
        await manager.start(
            run_id="run-2",
            session_id="session-2",
            user_id="user-1",
            assistant_message_id="message-2",
            snapshot_provider=_snapshot("run-2"),
            producer=producer,
        )
    release.set()
    await first.producer_task


@pytest.mark.asyncio
async def test_hitl_resume_keeps_run_identity_and_sequence() -> None:
    async def first_producer(publish):
        await publish("before-hitl")

    manager = RunManager()
    handle = await manager.start(
        run_id="run-1",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="message-1",
        snapshot_provider=_snapshot(),
        producer=first_producer,
    )
    await handle.producer_task
    await manager.transition("run-1", RunStatus.HITL_PENDING)

    async def resumed_producer(publish):
        await publish("after-hitl")

    resumed = await manager.resume("run-1", resumed_producer)
    await resumed.producer_task
    assert resumed.run_id == "run-1"
    assert resumed.assistant_message_id == "message-1"
    assert [event.sequence for event in resumed.buffer] == [1, 2]


@pytest.mark.asyncio
async def test_run_duration_limit_cancels_producer_with_stable_reason() -> None:
    observed = asyncio.Event()
    errors = []

    async def producer(publish):
        await asyncio.Event().wait()

    async def on_limit(error):
        errors.append(error)
        observed.set()

    manager = RunManager(max_run_duration_seconds=0.01)
    handle = await manager.start(
        run_id="run-1",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="message-1",
        snapshot_provider=_snapshot(),
        producer=producer,
        limit_handler=on_limit,
    )
    await asyncio.wait_for(observed.wait(), timeout=1)
    await asyncio.gather(handle.producer_task, return_exceptions=True)
    assert isinstance(errors[0], RunDurationExceeded)
    assert handle.limit_error.error_code == "RUN_TIMEOUT"


@pytest.mark.asyncio
async def test_run_output_limit_is_enforced_before_buffer_growth() -> None:
    async def producer(publish):
        await publish("x" * 2_000)

    manager = RunManager(max_output_bytes=256)
    handle = await manager.start(
        run_id="run-1",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="message-1",
        snapshot_provider=_snapshot(),
        producer=producer,
    )
    results = await asyncio.gather(handle.producer_task, return_exceptions=True)
    assert isinstance(results[0], RunOutputExceeded)
    assert handle.buffer_bytes == 0


@pytest.mark.asyncio
async def test_hitl_pending_expires_and_cannot_resume() -> None:
    expired = asyncio.Event()
    errors = []

    async def producer(publish):
        return None

    async def on_limit(error):
        errors.append(error)
        expired.set()

    manager = RunManager(hitl_pending_timeout_seconds=0.01)
    handle = await manager.start(
        run_id="run-1",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="message-1",
        snapshot_provider=_snapshot(),
        producer=producer,
        limit_handler=on_limit,
    )
    await handle.producer_task
    await manager.transition("run-1", RunStatus.HITL_PENDING)
    await asyncio.wait_for(expired.wait(), timeout=1)
    assert isinstance(errors[0], HitlPendingExpired)
    assert handle.status == RunStatus.ERROR
    with pytest.raises(ValueError):
        await manager.resume("run-1", producer)


@pytest.mark.asyncio
async def test_hitl_timeout_handler_survives_resume_and_second_pause() -> None:
    """多次 HITL 后仍必须通过同一 limit handler 持久化超时终态。"""
    expired = asyncio.Event()
    errors = []

    async def producer(publish):
        return None

    async def on_limit(error):
        errors.append(error)
        expired.set()

    manager = RunManager(hitl_pending_timeout_seconds=0.01)
    handle = await manager.start(
        run_id="run-repeat-hitl",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="message-1",
        snapshot_provider=_snapshot(),
        producer=producer,
        limit_handler=on_limit,
    )
    await handle.producer_task
    await manager.transition("run-repeat-hitl", RunStatus.HITL_PENDING)
    await manager.resume("run-repeat-hitl", producer)
    await handle.producer_task
    await manager.transition("run-repeat-hitl", RunStatus.HITL_PENDING)

    await asyncio.wait_for(expired.wait(), timeout=1)

    assert len(errors) == 1
    assert isinstance(errors[0], HitlPendingExpired)


@pytest.mark.asyncio
async def test_terminal_retention_releases_run_handle() -> None:
    async def producer(publish):
        return None

    manager = RunManager(terminal_retention_seconds=0.01)
    handle = await manager.start(
        run_id="run-1",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="message-1",
        snapshot_provider=_snapshot(),
        producer=producer,
    )
    await handle.producer_task
    await manager.transition("run-1", RunStatus.COMPLETED)
    await asyncio.sleep(0.03)
    with pytest.raises(KeyError):
        manager.get("run-1")


@pytest.mark.asyncio
async def test_stale_attempt_event_is_dropped_before_sequence_assignment() -> None:
    release = asyncio.Event()

    async def producer(publish):
        await release.wait()

    manager = RunManager()
    handle = await manager.start(
        run_id="run-1",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="message-1",
        snapshot_provider=_snapshot(),
        producer=producer,
    )
    await manager.advance_attempt("run-1", 2)
    with pytest.raises(StaleAttemptEvent):
        await manager.publish_attempt("run-1", "late", attempt_id=1)
    assert handle.last_sequence == 0
    current = await manager.publish_attempt("run-1", "current", attempt_id=2)
    assert current.sequence == 1
    release.set()
    await handle.producer_task
    metrics = manager.metrics_snapshot()
    assert metrics["stale_attempt_events"] == 1
    assert metrics["published_events"] == 1
    assert metrics["event_buffer_bytes"] > 0


@pytest.mark.asyncio
async def test_metrics_track_subscriber_overflow_and_reconnect() -> None:
    release = asyncio.Event()

    async def producer(publish):
        await release.wait()
        await publish("one")
        await publish("two")

    manager = RunManager(subscriber_queue_events=1)
    handle = await manager.start(
        run_id="run-1",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="message-1",
        snapshot_provider=_snapshot(),
        producer=producer,
    )
    await manager.subscribe("run-1", after_sequence=3)
    release.set()
    await handle.producer_task
    metrics = manager.metrics_snapshot()
    assert metrics["reconnect_subscriptions"] == 1
    assert metrics["subscriber_overflow"] == 1


@pytest.mark.asyncio
async def test_delivery_failure_does_not_cancel_producer_or_other_deliveries() -> None:
    manager = RunManager(terminal_retention_seconds=60)
    persisted: list[str] = []
    channel: list[str] = []
    producer_completed = asyncio.Event()

    async def persist_handler(envelope: SequencedRunEvent) -> None:
        persisted.append(envelope.event)

    async def broken_sse_handler(envelope: SequencedRunEvent) -> None:
        raise ConnectionError("browser disconnected")

    async def channel_handler(envelope: SequencedRunEvent) -> None:
        channel.append(envelope.event)

    async def producer(publish) -> None:
        await publish("first")
        await asyncio.sleep(0)
        await publish("second")
        producer_completed.set()

    handle = await manager.start(
        run_id="delivery-isolation",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="assistant-1",
        snapshot_provider=_snapshot("delivery-isolation"),
        producer=producer,
        deliveries={
            "persist": persist_handler,
            "sse:test": broken_sse_handler,
            "channel:telegram": channel_handler,
        },
    )

    await handle.producer_task
    await manager.drain_delivery(handle.run_id, "persist")
    await manager.drain_delivery(handle.run_id, "channel:telegram")

    assert producer_completed.is_set()
    assert persisted == ["first", "second"]
    assert channel == ["first", "second"]
    assert isinstance(handle.delivery_failures["sse:test"], ConnectionError)
    assert manager.metrics_snapshot()["delivery_failures"] == 1
    await manager.shutdown(drain_seconds=0)
