"""重试帧 × run 状态机的读路径契约。

背景（2026-09-03 接口测试轮问题 1/2/5）：kilo 网关 429 触发 LLM 重试中间件
发 ``run-status(retrying, attempt_id=N)`` 帧，两条独立缺陷叠加——

1. 终态事件应用在 projection 的 clone 上（pending_terminal 流程），
   ``handle.state`` 在 commit 时换绑，但 ``snapshot_provider`` 仍指向原始
   projection。若原始 projection 停在 retrying，commit 后 GET /runs/{id} 在
   整个 300s 终态保留窗口内返回 ``retrying`` + ``finish_reason=None``。
2. 重试计数器（单次模型调用的位次）被当成 run 级 attempt 抬升：第二次重试
   把 handle.attempt_id 抬到 2 后，任何带 attempt=1 戳的在途帧被 apply_event
   判为 StaleAttemptEvent 致命异常，杀死 producer、run 以 RUN_FAILED 收尾。
"""

from __future__ import annotations

import asyncio

import pytest

from noesis.chat.delivery.events import RunCompleted, WireFrame
from noesis.chat.runs import RunManager, RunStatus
from noesis.chat.runs.manager import TerminalCommitResult
from noesis.chat.runs.projection import RunProjection


def _projection(run_id: str) -> RunProjection:
    return RunProjection(
        run_id=run_id,
        user_id="user-1",
        session_id="session-1",
        assistant_message_id="message-1",
        qa_type="SUPER_AGENT_QA",
        status=RunStatus.RUNNING,
        attempt_id=1,
    )


async def _committed_terminal_handler(candidate) -> TerminalCommitResult:
    return TerminalCommitResult("committed")


def _retry_frame(attempt_id: int | None = None) -> WireFrame:
    data: dict = {"type": "run-status", "status": "retrying"}
    if attempt_id is not None:
        data["attempt_id"] = attempt_id
    return WireFrame(event="run-status", data=data)


async def _drive_to_terminal(manager: RunManager, run_id: str, producer) -> None:
    """按 start_queued_run 的接线（state=projection, provider=projection.snapshot）
    注册 run 并跑完 producer，等待终态落定。"""
    projection = _projection(run_id)
    await manager.start(
        run_id=run_id,
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="message-1",
        snapshot_provider=projection.snapshot,
        producer=producer,
        attempt_id=1,
        state=projection,
        terminal_handler=_committed_terminal_handler,
    )
    handle = manager.get(run_id)
    await asyncio.wait_for(handle.terminal_future, timeout=2)


# ---------- projection 状态机 ----------


def test_run_status_retrying_is_not_run_lifecycle_status() -> None:
    """retrying 是单次模型调用的瞬态，不得写入 run 状态；帧本身照常放行 fan-out。"""
    projection = _projection("run-p1")

    applied = projection.apply(_retry_frame())

    assert applied is True
    assert projection.status is RunStatus.RUNNING


def test_run_status_frame_never_advances_run_attempt() -> None:
    """重试位次是遥测字段（model_calls.attempt），不得抬升 run 级 attempt。"""
    projection = _projection("run-p2")

    projection.apply(_retry_frame(attempt_id=2))

    assert projection.attempt_id == 1


# ---------- manager 级：终态读路径与 producer 存活 ----------


@pytest.mark.asyncio
async def test_terminal_commit_rebinds_snapshot_provider() -> None:
    """经重试后完成的 run：commit 后 snapshot 必须报终态与 finish_reason。"""
    manager = RunManager()

    async def producer(publish) -> None:
        await publish(_retry_frame(), 1)
        await publish(WireFrame(event="text-delta", data={"text_delta": "answer"}), 1)
        await publish(RunCompleted(finish_reason="stop", usage={"steps": 1}), 1)

    await _drive_to_terminal(manager, "run-retry", producer)
    handle = manager.get("run-retry")

    snapshot = handle.snapshot_provider(
        handle.last_sequence, handle.status, handle.attempt_id
    )
    assert handle.status is RunStatus.COMPLETED
    assert snapshot.status is RunStatus.COMPLETED, (
        f"终态后快照仍为 {snapshot.status.value}（snapshot_provider 未随终态换绑）"
    )
    assert snapshot.finish_reason == "stop"


@pytest.mark.asyncio
async def test_subscriber_receives_terminal_envelope_after_retry() -> None:
    """重试后完成的 run：订阅者在终态 commit 后必须收到终态 envelope。"""
    manager = RunManager()
    started = asyncio.Event()

    async def gated_producer(publish) -> None:
        await started.wait()
        await publish(_retry_frame(), 1)
        await publish(RunCompleted(finish_reason="stop", usage={"steps": 1}), 1)

    projection = _projection("run-sub")
    await manager.start(
        run_id="run-sub",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="message-1",
        snapshot_provider=projection.snapshot,
        producer=gated_producer,
        attempt_id=1,
        state=projection,
        terminal_handler=_committed_terminal_handler,
    )
    subscription = await manager.subscribe("run-sub")
    started.set()
    handle = manager.get("run-sub")
    await asyncio.wait_for(handle.terminal_future, timeout=2)

    # 先排掉 run-status 等瞬态帧，终态 envelope 必须在超时前到达
    terminal = None
    while True:
        item = await asyncio.wait_for(subscription.queue.get(), timeout=1)
        if isinstance(item.event, RunCompleted):
            terminal = item
            break
    assert terminal is not None


@pytest.mark.asyncio
async def test_frame_attempt_ordinal_does_not_kill_producer() -> None:
    """带旧 attempt 戳的迟到帧不得以致命异常杀死 producer。

    复现 2026-09-03 run 82f2045c/eaf1224f：重试帧（attempt_id=2）抬升 run
    attempt 后，attempt=1 的在途帧触发 StaleAttemptEvent，run 误判 RUN_FAILED。
    修复后：帧的 attempt 戳只是遥测位次，producer 用 run attempt 发布。
    """
    manager = RunManager()

    async def producer(publish) -> None:
        # 第一次调用失败 → 重试帧（payload attempt_id=2，与现网中间件一致）
        await publish(_retry_frame(attempt_id=2), 1)
        # 重试后的调用帧：bridge 按调用起点戳 attempt=1（per-call 位次）
        late_frame = WireFrame(
            event="text-delta",
            data={"text_delta": "recovered"},
            attempt_id=1,
        )
        await publish(late_frame, 1)
        await publish(RunCompleted(finish_reason="stop", usage={"steps": 1}), 1)

    # 当前实现：late_frame 的 attempt 戳经 producer 透传进 apply_event，
    # 与被重试帧抬升的 handle.attempt_id=2 不等 → StaleAttemptEvent 杀死
    # producer，terminal_future 永不落定，wait_for 超时让回归红得直白。
    await _drive_to_terminal(manager, "run-stale", producer)
    handle = manager.get("run-stale")
    assert handle.status is RunStatus.COMPLETED
