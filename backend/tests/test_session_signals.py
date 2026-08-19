"""Session 信令总线与 RunManager 状态挂钩的回归测试。"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from noesis.chat.runs import RunManager, RunSnapshot, RunStatus, session_signal_bus
from noesis.chat.runs.session_signals import MAX_SUBSCRIBERS_PER_SESSION


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


async def _start_run(manager: RunManager, producer) -> None:
    await manager.start(
        run_id="run-1",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="message-1",
        snapshot_provider=_snapshot(),
        producer=producer,
    )


def _drain(queue: asyncio.Queue) -> list[dict]:
    signals = []
    while not queue.empty():
        signals.append(queue.get_nowait())
    return signals


@pytest.fixture(autouse=True)
def _reset_bus():
    session_signal_bus._subscribers.clear()
    yield
    session_signal_bus._subscribers.clear()


def test_bus_publish_reaches_subscriber_and_cleans_up() -> None:
    queue = session_signal_bus.subscribe("user-1", "session-1")
    assert queue is not None
    session_signal_bus.publish("user-1", "session-1", {"type": "run-started", "run_id": "run-1"})
    # 其它会话不受影响
    session_signal_bus.publish("user-2", "session-1", {"type": "run-started", "run_id": "run-x"})
    assert _drain(queue) == [{"type": "run-started", "run_id": "run-1"}]

    session_signal_bus.unsubscribe("user-1", "session-1", queue)
    assert ("user-1", "session-1") not in session_signal_bus._subscribers


def test_bus_subscriber_limit() -> None:
    queues = [session_signal_bus.subscribe("user-1", "session-1") for _ in range(MAX_SUBSCRIBERS_PER_SESSION)]
    assert all(q is not None for q in queues)
    assert session_signal_bus.subscribe("user-1", "session-1") is None


def test_bus_drops_on_slow_subscriber_without_raising() -> None:
    queue = session_signal_bus.subscribe("user-1", "session-1")
    assert queue is not None
    for index in range(queue.maxsize + 8):
        session_signal_bus.publish("user-1", "session-1", {"type": "tick", "i": index})
    assert queue.qsize() == queue.maxsize
    # 满队列后继续发布不抛异常
    session_signal_bus.publish("user-1", "session-1", {"type": "tick", "i": 999})


@pytest.mark.asyncio
async def test_run_lifecycle_emits_started_and_terminal_signals() -> None:
    release = asyncio.Event()

    async def producer(publish):
        await publish({"type": "text-delta", "delta": "a"})
        await release.wait()

    manager = RunManager()
    queue = session_signal_bus.subscribe("user-1", "session-1")
    assert queue is not None
    await _start_run(manager, producer)

    # start() 直接置 RUNNING：订阅者收到 run-started
    started = await asyncio.wait_for(queue.get(), timeout=5)
    assert started["type"] == "run-started"
    assert started["run_id"] == "run-1"
    assert started["assistant_message_id"] == "message-1"

    # 终态信号挂在 transition()：生产路径由持久化层驱动迁移，这里直接验证挂钩
    release.set()
    handle = manager.get("run-1")
    handle.producer_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await handle.producer_task
    await manager.transition("run-1", RunStatus.COMPLETED)
    terminal = await asyncio.wait_for(queue.get(), timeout=5)
    assert terminal == {"type": "run-terminal", "run_id": "run-1", "status": "completed"}


@pytest.mark.asyncio
async def test_hitl_pending_transition_emits_signal() -> None:
    release = asyncio.Event()

    async def producer(publish):
        await release.wait()

    manager = RunManager()
    queue = session_signal_bus.subscribe("user-1", "session-1")
    assert queue is not None
    await _start_run(manager, producer)
    await asyncio.wait_for(queue.get(), timeout=5)  # 丢弃 run-started

    await manager.transition("run-1", RunStatus.HITL_PENDING)
    signal = await asyncio.wait_for(queue.get(), timeout=5)
    assert signal == {"type": "run-hitl-pending", "run_id": "run-1"}
