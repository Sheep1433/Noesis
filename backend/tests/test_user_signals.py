"""用户级信令总线与 RunManager 状态挂钩的回归测试（会话列表实时刷新）。"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from noesis.chat.runs import RunManager, RunSnapshot, RunStatus, user_signal_bus
from noesis.chat.runs.user_signals import MAX_SUBSCRIBERS_PER_USER


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
    user_signal_bus._subscribers.clear()
    yield
    user_signal_bus._subscribers.clear()


def test_bus_publish_reaches_subscriber_and_cleans_up() -> None:
    queue = user_signal_bus.subscribe("user-1")
    assert queue is not None
    user_signal_bus.publish(
        "user-1", {"type": "run-started", "session_id": "session-1"}
    )
    # 其它用户不受影响
    user_signal_bus.publish(
        "user-2", {"type": "run-started", "session_id": "session-x"}
    )
    assert _drain(queue) == [{"type": "run-started", "session_id": "session-1"}]

    user_signal_bus.unsubscribe("user-1", queue)
    assert "user-1" not in user_signal_bus._subscribers


def test_bus_subscriber_limit() -> None:
    queues = [
        user_signal_bus.subscribe("user-1") for _ in range(MAX_SUBSCRIBERS_PER_USER)
    ]
    assert all(q is not None for q in queues)
    assert user_signal_bus.subscribe("user-1") is None


def test_bus_drops_on_slow_subscriber_without_raising() -> None:
    queue = user_signal_bus.subscribe("user-1")
    assert queue is not None
    for index in range(queue.maxsize + 8):
        user_signal_bus.publish("user-1", {"type": "tick", "i": index})
    assert queue.qsize() == queue.maxsize
    # 满队列后继续发布不抛异常
    user_signal_bus.publish("user-1", {"type": "tick", "i": 999})


@pytest.mark.asyncio
async def test_run_lifecycle_emits_user_signals_with_session_and_status() -> None:
    """start → running；终态 → terminal；用户级信令带 session_id + status。"""
    release = asyncio.Event()

    async def producer(publish):
        await publish({"type": "text-delta", "delta": "a"})
        await release.wait()

    manager = RunManager()
    queue = user_signal_bus.subscribe("user-1")
    assert queue is not None
    await _start_run(manager, producer)

    started = await asyncio.wait_for(queue.get(), timeout=5)
    assert started["type"] == "run-started"
    assert started["session_id"] == "session-1"
    assert started["status"] == "running"

    release.set()
    handle = manager.get("run-1")
    handle.producer_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await handle.producer_task
    await manager.transition("run-1", RunStatus.COMPLETED)
    terminal = await asyncio.wait_for(queue.get(), timeout=5)
    assert terminal["type"] == "run-terminal"
    assert terminal["session_id"] == "session-1"
    assert terminal["status"] == "completed"


@pytest.mark.asyncio
async def test_hitl_pending_emits_user_signal() -> None:
    release = asyncio.Event()

    async def producer(publish):
        await release.wait()

    manager = RunManager()
    queue = user_signal_bus.subscribe("user-1")
    assert queue is not None
    await _start_run(manager, producer)
    await asyncio.wait_for(queue.get(), timeout=5)  # 丢弃 run-started

    await manager.transition("run-1", RunStatus.HITL_PENDING)
    signal = await asyncio.wait_for(queue.get(), timeout=5)
    assert signal["type"] == "run-hitl-pending"
    assert signal["session_id"] == "session-1"
    assert signal["status"] == "hitl_pending"


@pytest.mark.asyncio
async def test_terminal_signal_via_production_apply_event_path() -> None:
    """生产终态路径：apply_event + terminal_handler 落库回调（不经 transition()）。

    transition() 唯一生产调用方是测试用例续跑的 _persist_projection；主对话
    路径的终态经 _commit_terminal_candidate 直接赋 handle.status——回归保证
    该路径同样发布 run-terminal（否则会话列表徽章完成不清）。
    """
    from noesis.chat.runs.manager import TerminalCommitResult
    from noesis.chat.runs.models import RunStatus as _RS
    from noesis.chat.runs.projection import RunProjection
    from noesis.chat.delivery.events import RunCompleted

    async def producer(publish):
        await publish({"type": "text-delta", "delta": "好"})

    manager = RunManager()
    queue = user_signal_bus.subscribe("user-1")
    assert queue is not None

    projection = RunProjection(
        run_id="run-1",
        user_id="user-1",
        session_id="session-1",
        assistant_message_id="message-1",
        qa_type="COMMON_QA",
    )

    async def terminal_handler(candidate):
        return TerminalCommitResult("committed")

    await manager.start(
        run_id="run-1",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="message-1",
        snapshot_provider=_snapshot(),
        producer=producer,
        state=projection,
        terminal_handler=terminal_handler,
    )
    started = await asyncio.wait_for(queue.get(), timeout=5)
    assert started["type"] == "run-started"

    # 生产等价：apply_event 投递终态事件 → terminal candidate → handler 提交
    await manager.apply_event("run-1", RunCompleted(finish_reason="stop"))
    terminal = await asyncio.wait_for(queue.get(), timeout=5)
    assert terminal["type"] == "run-terminal"
    assert terminal["session_id"] == "session-1"
    assert terminal["status"] == _RS.COMPLETED.value
