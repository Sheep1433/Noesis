"""Phase 1 失败基线测试 → Phase 2 更新。

原 Phase 1 测试稳定复现 4 个正确性缺陷。Phase 2 把 projection.apply 移入
RunHandle lock 并引入 producer_generation 后：
- §1.1 race 已修复 → 翻转为断言 snapshot sequence 与 projection 一致
- §1.2 checkpoint 读 live projection 未完全修复（待 Phase 3 immutable snapshot）→ 保留 bug 演示
- §1.3 terminal 先可见后持久化未修复（待 Phase 3 finalize barrier）→ 保留 bug 演示
- §1.4 stale producer generation 已修复 → 翻转为断言 StaleProducerGeneration 被抛出

对应 tasks.md §1.1–§1.4。
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from noesis.chat.delivery.events import (
    RunCompleted,
    StreamDone,
    WireFrame,
    wire_frame,
)
from noesis.services.persist_sink import PersistSink
from noesis.chat.runs import (
    CheckpointRequest,
    TerminalCommitResult,
    RunManager,
    RunSnapshot,
    RunStatus,
    SequencedRunEvent,
    StaleAttemptEvent,
    StaleProducerGeneration,
)
from noesis.chat.runs.manager import PersistWriter
from noesis.services.run_service import RunProjection, RunService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_projection(
    run_id: str = "run-1",
    session_id: str = "session-1",
    assistant_message_id: str = "msg-1",
) -> RunProjection:
    return RunProjection(
        run_id=run_id,
        user_id="user-1",
        session_id=session_id,
        assistant_message_id=assistant_message_id,
        qa_type="COMMON_QA",
    )


def _snapshot_provider(projection: RunProjection):
    return projection.snapshot


def _text_delta(text: str) -> WireFrame:
    return wire_frame("text-delta", {"text_delta": text})


# ---------------------------------------------------------------------------
# §1.1  apply/subscribe 原子性（Phase 2 已修复）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_and_subscribe_are_atomic_no_race() -> None:
    """Phase 2 修复后：projection.apply 在 RunHandle lock 内执行，
    subscribe 在同一 lock 内读 snapshot。不存在"snapshot sequence=N-1 但
    projection 已含 N"的竞态。

    验证：subscribe 在 apply_event 执行期间调用时，snapshot sequence 与
    projection 内容一致——要么都是 N-1（apply 前），要么都是 N（apply 后）。
    """
    projection = _make_projection()
    manager = RunManager()

    async def producer(publish) -> None:
        # apply_event 在 lock 内原子完成 apply + sequence
        await RunService.publish_projected_event(
            "run-1", projection, _text_delta("event-1"), publish
        )

    handle = await manager.start(
        run_id="run-1",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="msg-1",
        snapshot_provider=_snapshot_provider(projection),
        producer=producer,
        state=projection,
    )
    await handle.producer_task

    # apply 完成后 subscribe 应看到 sequence=1 且 parts 包含 event-1
    subscription = await manager.subscribe("run-1")
    assert subscription.snapshot.sequence == 1
    assert "event-1" in str(subscription.snapshot.parts)


# ---------------------------------------------------------------------------
# §1.2  checkpoint 读 live projection（待 Phase 3 immutable snapshot 修复）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkpoint_uses_immutable_snapshot_not_live_projection() -> None:
    """Phase 3 修复后：apply_event 在 lock 内捕获 immutable snapshot 并附加到 envelope。
    persist_delivery 使用 envelope.checkpoint_snapshot（与 sequence 绑定），
    不再读取 live projection。即使 producer 在 checkpoint 写入期间继续发事件，
    DB 写入的 snapshot 内容严格对应写入时的 sequence。

    验证：checkpoint sequence=1 的 snapshot 只包含 event-1，不包含后续 event-2/event-3。
    """
    from noesis.chat.runs import RunManager, RunStatus
    from noesis.services.persist_sink import PersistSink
    from noesis.services.run_service import RunProjection, RunService

    projection = RunProjection(
        run_id="run-cp",
        user_id="user-1",
        session_id="session-1",
        assistant_message_id="msg-cp",
        qa_type="COMMON_QA",
    )
    persist_sink = PersistSink(checkpoint_interval_seconds=0)
    manager = RunManager()

    checkpoint_ready = asyncio.Event()
    checkpoint_blocked = asyncio.Event()
    captured: dict[str, Any] = {}

    async def persist_delivery(envelope: SequencedRunEvent) -> None:
        if envelope.checkpoint_snapshot is not None:
            checkpoint_ready.set()
            await checkpoint_blocked.wait()
            captured["sequence"] = envelope.sequence
            captured["snapshot"] = envelope.checkpoint_snapshot
            captured["parts"] = list(envelope.checkpoint_snapshot.parts)

    async def producer(publish) -> None:
        await RunService.publish_projected_event(
            "run-cp", projection, _text_delta("event-1"), publish
        )
        await asyncio.sleep(0)
        await asyncio.wait_for(checkpoint_ready.wait(), timeout=2)
        # 在 checkpoint 写入期间继续发事件
        await RunService.publish_projected_event(
            "run-cp", projection, _text_delta("event-2"), publish
        )
        await RunService.publish_projected_event(
            "run-cp", projection, _text_delta("event-3"), publish
        )
        checkpoint_blocked.set()
        await manager.drain_delivery("run-cp", "persist")

    handle = await manager.start(
        run_id="run-cp",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="msg-cp",
        snapshot_provider=projection.snapshot,
        producer=producer,
        deliveries={"persist": persist_delivery},
        state=projection,
        checkpoint_policy=lambda event, seq: "semantic" if seq == 1 else None,
    )
    await handle.producer_task

    # 修复后：checkpoint sequence=1 的 snapshot 只包含 event-1
    assert captured["sequence"] == 1
    parts_text = str(captured["parts"])
    assert "event-1" in parts_text
    # event-2/event-3 不在 checkpoint snapshot 中（它们在后续 sequence）
    assert "event-2" not in parts_text
    assert "event-3" not in parts_text


@pytest.mark.asyncio
async def test_late_checkpoint_does_not_overwrite_newer_snapshot() -> None:
    """Phase 3 修复后：_persist_checkpoint 的 UPDATE 带 last_sequence <= sequence 条件，
    迟到 checkpoint 不覆盖更新的 DB snapshot。
    """
    from sqlalchemy import create_engine, text
    import json as _json

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE t_agent_run (id TEXT PRIMARY KEY, status TEXT, "
                "last_sequence INTEGER, snapshot TEXT)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO t_agent_run (id, status, last_sequence, snapshot) "
                "VALUES ('run-late', 'running', 50, :snap)"
            ),
            {"snap": _json.dumps({"parts": [{"text": "fifty"}]})},
        )
    # Phase 3：UPDATE 带 last_sequence <= sequence guard
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE t_agent_run SET last_sequence = :seq, snapshot = :snap "
                "WHERE id = 'run-late' AND status = 'running' AND last_sequence <= :seq"
            ),
            {"seq": 45, "snap": _json.dumps({"parts": [{"text": "forty-five-late"}]})},
        )
        # sequence=45 < stored=50，guard 拒绝更新
        assert result.rowcount == 0

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT last_sequence, snapshot FROM t_agent_run WHERE id = 'run-late'")
        ).one()
        # 修复后：sequence 保持 50，snapshot 不被覆盖
        assert row.last_sequence == 50
        assert "fifty" in row.snapshot
    engine.dispose()


# ---------------------------------------------------------------------------
# §1.3  terminal 先对 Delivery 可见、后持久化失败（待 Phase 3 finalize 修复）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminal_not_visible_until_persistence_succeeds() -> None:
    """Phase 3 修复后：terminal 事件（RunCompleted）只 apply + buffer，不 fan-out。
    fan-out 延迟到 transition()（DB 写入成功后）。如果 DB 写失败（producer 抛异常），
    subscriber 永远不会看到 completed 伪终态。
    """
    projection = _make_projection()
    manager = RunManager()

    subscriber_events: list[Any] = []

    async def sse_handler(envelope: SequencedRunEvent) -> None:
        subscriber_events.append(envelope.event)

    async def producer(publish) -> None:
        await RunService.publish_projected_event(
            "run-1", projection, _text_delta("content"), publish
        )
        # RunCompleted 被 apply 但不 fan-out（pending_terminal_envelope）
        await RunService.publish_projected_event(
            "run-1", projection, RunCompleted(finish_reason="stop"), publish
        )
        # 模拟 DB 写入失败：producer 在 _persist_projection 前抛异常
        raise RuntimeError("DB write failed before terminal persistence")

    with patch.object(
        RunService, "_persist_cancel_or_error", new=AsyncMock(return_value=None)
    ):
        handle = await manager.start(
            run_id="run-1",
            session_id="session-1",
            user_id="user-1",
            assistant_message_id="msg-1",
            snapshot_provider=_snapshot_provider(projection),
            producer=producer,
            deliveries={"sse": sse_handler},
            state=projection,
        )
        results = await asyncio.gather(handle.producer_task, return_exceptions=True)
        assert isinstance(results[0], Exception)

    # 修复后：subscriber 未收到 RunCompleted（terminal 被暂存，未 fan-out）
    assert not any(isinstance(e, RunCompleted) for e in subscriber_events)
    # text-delta 仍正常收到（非 terminal 立即 fan-out）
    assert len(subscriber_events) >= 1
    # 未提交 terminal 不得伪造 completed/error；Run 保持非终态等待权威持久化。
    assert handle.status == RunStatus.RUNNING


@pytest.mark.asyncio
async def test_terminal_candidate_is_not_replayable_before_commit() -> None:
    """Terminal candidate 不能在数据库提交前进入 replay buffer。"""
    projection = _make_projection(run_id="run-terminal-replay")
    manager = RunManager()
    release = asyncio.Event()

    async def producer(publish) -> None:
        await publish(_text_delta("visible"), attempt_id=1)
        await publish(RunCompleted(finish_reason="stop"), attempt_id=1)
        await release.wait()

    handle = await manager.start(
        run_id="run-terminal-replay",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="msg-1",
        snapshot_provider=projection.snapshot,
        producer=producer,
        state=projection,
    )
    while handle.last_sequence < 1:
        await asyncio.sleep(0)

    subscription = await manager.subscribe("run-terminal-replay", after_sequence=1)
    assert not any(isinstance(item.event, RunCompleted) for item in subscription.replay)
    assert subscription.snapshot.status == RunStatus.RUNNING

    release.set()
    await handle.producer_task


@pytest.mark.asyncio
async def test_terminal_is_fanned_out_only_after_commit_returns() -> None:
    """terminal transaction 返回 committed 前，live snapshot/replay/queue 均保持非终态。"""
    projection = _make_projection(run_id="run-terminal-barrier")
    commit_started = asyncio.Event()
    allow_commit = asyncio.Event()

    async def terminal_handler(candidate) -> TerminalCommitResult:
        assert candidate.envelope.sequence == 2
        commit_started.set()
        await allow_commit.wait()
        return TerminalCommitResult("committed")

    manager = RunManager(terminal_persistence_budget_seconds=1)

    async def producer(publish) -> None:
        await publish(_text_delta("visible"), attempt_id=1)
        await publish(RunCompleted(finish_reason="stop"), attempt_id=1)

    handle = await manager.start(
        run_id="run-terminal-barrier",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="msg-1",
        snapshot_provider=projection.snapshot,
        producer=producer,
        state=projection,
        terminal_handler=terminal_handler,
    )
    subscription = await manager.subscribe("run-terminal-barrier")
    await asyncio.wait_for(commit_started.wait(), timeout=1)

    assert handle.status == RunStatus.RUNNING
    assert handle.last_sequence == 1
    assert subscription.queue.qsize() == 1
    assert (await manager.subscribe("run-terminal-barrier")).snapshot.status == RunStatus.RUNNING

    allow_commit.set()
    await handle.producer_task
    queued = [subscription.queue.get_nowait(), subscription.queue.get_nowait()]
    assert isinstance(queued[-1].event, RunCompleted)
    assert handle.status == RunStatus.COMPLETED
    assert handle.last_sequence == 2
    await manager.remove_terminal(handle.run_id)


@pytest.mark.asyncio
async def test_terminal_persistence_blocked_stops_producer() -> None:
    """同步 persistence budget 耗尽后停止上游，只保留 immutable candidate 重试。"""
    projection = _make_projection(run_id="run-terminal-blocked")
    continued = asyncio.Event()

    async def terminal_handler(_candidate) -> TerminalCommitResult:
        return TerminalCommitResult("failed")

    manager = RunManager(
        terminal_persistence_budget_seconds=0.01,
        terminal_retry_interval_seconds=60,
    )

    async def producer(publish) -> None:
        await publish(RunCompleted(finish_reason="stop"), attempt_id=1)
        await asyncio.sleep(0)
        continued.set()
        await asyncio.sleep(60)

    handle = await manager.start(
        run_id="run-terminal-blocked",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="msg-1",
        snapshot_provider=projection.snapshot,
        producer=producer,
        state=projection,
        terminal_handler=terminal_handler,
    )
    result = await asyncio.wait_for(
        asyncio.gather(handle.producer_task, return_exceptions=True), timeout=1
    )

    assert isinstance(result[0], asyncio.CancelledError)
    assert handle.cancel_requested is True
    assert continued.is_set() is False
    assert handle.pending_terminal is not None
    assert handle.status == RunStatus.RUNNING
    await manager.shutdown(drain_seconds=0)


@pytest.mark.asyncio
async def test_persist_writer_keeps_only_latest_pending_checkpoint() -> None:
    """writer 正在写 N 时，N+1..N+20 只保留 sequence 最大的一份。"""
    started = asyncio.Event()
    release = asyncio.Event()
    persisted: list[int] = []

    async def handler(request: CheckpointRequest) -> None:
        persisted.append(request.snapshot_sequence)
        if request.snapshot_sequence == 1:
            started.set()
            await release.wait()

    writer = PersistWriter(handler, retry_interval_seconds=0.01)
    snapshot = _make_projection(run_id="run-writer").snapshot(0, RunStatus.RUNNING, 1)
    writer.submit(CheckpointRequest("run-writer", "msg-1", 1, snapshot, "coalescible"))
    await asyncio.wait_for(started.wait(), timeout=1)
    for sequence in range(2, 21):
        writer.submit(
            CheckpointRequest("run-writer", "msg-1", sequence, snapshot, "coalescible")
        )
    release.set()
    await asyncio.wait_for(writer.drain(), timeout=1)
    assert persisted == [1, 20]
    await writer.close()


# ---------------------------------------------------------------------------
# §1.4  stale producer generation（Phase 2 已修复）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_attempt_late_delta_is_rejected() -> None:
    """验证 attempt_id 拒距机制：advance_attempt 后旧 attempt 事件被拒绝。"""
    projection = _make_projection()
    manager = RunManager()

    release = asyncio.Event()

    async def producer(publish) -> None:
        await release.wait()

    handle = await manager.start(
        run_id="run-1",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="msg-1",
        snapshot_provider=_snapshot_provider(projection),
        producer=producer,
        state=projection,
    )

    await manager.advance_attempt("run-1", 2)

    with pytest.raises(StaleAttemptEvent):
        await manager.publish_attempt("run-1", _text_delta("late"), attempt_id=1)

    assert handle.last_sequence == 0
    assert manager.metrics_snapshot()["stale_attempt_events"] == 1

    release.set()
    await handle.producer_task


@pytest.mark.asyncio
async def test_resume_isolates_old_producer_task_events_via_generation() -> None:
    """Phase 2 修复后：resume 递增 producer_generation，旧 producer task 持有的
    publish callable 因 generation 不匹配被拒绝（StaleProducerGeneration）。
    """
    projection = _make_projection()
    manager = RunManager()

    first_producer_done = asyncio.Event()
    old_publish_ref: list[Any] = []

    async def first_producer(publish) -> None:
        old_publish_ref.append(publish)
        await publish(_text_delta("before-hitl"), attempt_id=1)
        first_producer_done.set()

    handle = await manager.start(
        run_id="run-1",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="msg-1",
        snapshot_provider=_snapshot_provider(projection),
        producer=first_producer,
        state=projection,
    )
    await handle.producer_task
    await manager.transition("run-1", RunStatus.HITL_PENDING)

    resumed = await manager.resume(
        "run-1",
        producer=lambda publish: publish(_text_delta("after-hitl"), attempt_id=1),
    )
    await resumed.producer_task

    # 旧 producer task 的 publish callable generation=1，当前 generation=2
    old_publish = old_publish_ref[0]
    with pytest.raises(StaleProducerGeneration):
        await old_publish(_text_delta("late-from-old-task"), attempt_id=1)

    # 迟到事件未分配 sequence，未修改 projection
    assert "late-from-old-task" not in str(projection.builder.to_dict())
    assert manager.metrics_snapshot()["stale_producer_generation_events"] == 1
