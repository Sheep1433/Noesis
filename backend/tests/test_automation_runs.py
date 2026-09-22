"""自动化运行状态、幂等重试与调度边界（Phase 3：tick 异步化 + 交付链收口判定）。"""

import asyncio
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from noesis.storage.postgres.models.settings import TUserScheduledTaskRun
from noesis.services.scheduled_task_service import (
    ScheduledTaskService,
    compute_next_run_ms,
    cron_summary,
)


class _ScalarResult:
    def __init__(self, one=None, many=None):
        self._one = one
        self._many = many or []
        self.rowcount = 0

    def scalar_one_or_none(self):
        return self._one

    def scalar_one(self):
        return self._one

    def scalars(self):
        return SimpleNamespace(all=lambda: self._many)


def _task():
    return SimpleNamespace(
        id="task-1", user_id=1, prompt="daily report", qa_type="SUPER_AGENT_QA",
        session_binding="none", delivery="none", cron_expr="0 9 * * *",
        timezone="Asia/Shanghai", next_run_at=1, updated_at=1,
    )


def _run_record():
    return TUserScheduledTaskRun(
        id="run-1", task_id="task-1", user_id=1, status="queued",
        trigger_source="manual", idempotency_key="k", created_at=1,
    )


def _mock_db(one=None):
    return SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarResult(one=one)),
        add=lambda _row: None, commit=AsyncMock(), refresh=AsyncMock(),
    )


def _patch_delivery_chain(
    monkeypatch: pytest.MonkeyPatch,
    *,
    delivered: bool = True,
    outcome: tuple[bool, str] = (True, ""),
    latest_text: str = "",
) -> None:
    """隔离交付链依赖（executor 注册表 / 续跑唤醒 / 活跃 run / 最新 run 文本）。"""
    monkeypatch.setattr(
        ScheduledTaskService, "_await_session_delivery", AsyncMock(return_value=delivered)
    )
    monkeypatch.setattr(
        ScheduledTaskService, "_delivery_outcome", staticmethod(lambda session_id: outcome)
    )
    monkeypatch.setattr(
        ScheduledTaskService, "_latest_run_text", AsyncMock(return_value=latest_text)
    )


def test_cron_preview_and_dst_boundary() -> None:
    assert cron_summary("0 9 * * *", "Asia/Shanghai") == "每天 09:00（Asia/Shanghai）"
    assert cron_summary("0 9 * * 1,3,5", "Asia/Shanghai") == "周一、周三、周五 09:00（Asia/Shanghai）"
    before_dst = int(datetime(2026, 3, 7, 12, tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000)
    next_ms = compute_next_run_ms("30 2 * * *", "America/New_York", after_ms=before_dst)
    next_local = datetime.fromtimestamp(next_ms / 1000, ZoneInfo("America/New_York"))
    assert next_ms > before_dst
    assert next_local.date().isoformat() in {"2026-03-08", "2026-03-09"}


@pytest.mark.asyncio
async def test_successful_run_has_terminal_record(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = []
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarResult()), add=lambda row: captured.append(row),
        commit=AsyncMock(), refresh=AsyncMock(),
    )
    monkeypatch.setattr(ScheduledTaskService, "_execute_task", AsyncMock(return_value=SimpleNamespace(session_id="session-1", plain_text="done")))
    _patch_delivery_chain(monkeypatch)
    run = await ScheduledTaskService._execute_and_finalize(db, _task(), _run_record())
    assert run.status == "succeeded"
    assert run.started_at and run.finished_at
    assert run.session_id == "session-1"
    assert run.result_summary == "done"
    assert captured == []


@pytest.mark.asyncio
async def test_result_summary_prefers_final_delivery_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """result_summary 取 continuation 链最终 run 文本，而非首 run 的「已转后台」。"""
    db = _mock_db()
    monkeypatch.setattr(ScheduledTaskService, "_execute_task", AsyncMock(return_value=SimpleNamespace(session_id="session-1", plain_text="子任务已转后台")))
    _patch_delivery_chain(monkeypatch, latest_text="最终交付：调研结论 ABC")
    run = await ScheduledTaskService._execute_and_finalize(db, _task(), _run_record())
    assert run.status == "succeeded"
    assert run.result_summary == "最终交付：调研结论 ABC"


@pytest.mark.asyncio
async def test_subtask_failure_fails_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """交付链收口但存在失败/超时子任务：scheduled run 落 failed（不再自欺 succeeded）。"""
    db = _mock_db()
    monkeypatch.setattr(ScheduledTaskService, "_execute_task", AsyncMock(return_value=SimpleNamespace(session_id="session-1", plain_text="转后台")))
    _patch_delivery_chain(
        monkeypatch, outcome=(False, "子任务未交付完成（1 个失败/超时：市场调研）"),
    )
    run = await ScheduledTaskService._execute_and_finalize(db, _task(), _run_record())
    assert run.status == "failed"
    assert run.error_category == "subtask_failed"
    assert "市场调研" in run.error_message


@pytest.mark.asyncio
async def test_delivery_timeout_fails_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _mock_db()
    monkeypatch.setattr(ScheduledTaskService, "_execute_task", AsyncMock(return_value=SimpleNamespace(session_id="session-1", plain_text="转后台")))
    _patch_delivery_chain(monkeypatch, delivered=False)
    run = await ScheduledTaskService._execute_and_finalize(db, _task(), _run_record())
    assert run.status == "failed"
    assert run.error_category == "delivery_timeout"
    assert run.error_message


@pytest.mark.asyncio
async def test_notification_preference_does_not_stop_business_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from noesis.services.notification_preference_service import NotificationPreferenceService
    task = _task()
    task.delivery = "web_notify"
    db = _mock_db()
    execute = AsyncMock(return_value=SimpleNamespace(session_id="session-1", plain_text="done"))
    monkeypatch.setattr(ScheduledTaskService, "_execute_task", execute)
    monkeypatch.setattr(NotificationPreferenceService, "should_notify", AsyncMock(return_value=False))
    _patch_delivery_chain(monkeypatch)

    run = await ScheduledTaskService._execute_and_finalize(db, task, _run_record())

    execute.assert_awaited_once()
    assert run.status == "succeeded"
    assert run.delivery_result["status"] == "suppressed"


@pytest.mark.asyncio
async def test_enabled_web_notification_is_delivered_via_run_record(monkeypatch: pytest.MonkeyPatch) -> None:
    from noesis.services.notification_preference_service import NotificationPreferenceService

    task = _task()
    task.delivery = "web_notify"
    db = _mock_db()
    monkeypatch.setattr(ScheduledTaskService, "_execute_task", AsyncMock(return_value=SimpleNamespace(session_id="session-1", plain_text="done")))
    monkeypatch.setattr(NotificationPreferenceService, "should_notify", AsyncMock(return_value=True))
    _patch_delivery_chain(monkeypatch)

    run = await ScheduledTaskService._execute_and_finalize(db, task, _run_record())

    assert run.delivery_result == {"status": "delivered", "target": "web_notify", "surface": "web"}


@pytest.mark.asyncio
async def test_enabled_channel_notification_uses_delivery_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    from noesis.services.notification_preference_service import NotificationPreferenceService

    task = _task()
    task.delivery = "channel:channel-1"
    db = _mock_db()
    monkeypatch.setattr(ScheduledTaskService, "_execute_task", AsyncMock(return_value=SimpleNamespace(session_id="session-1", plain_text="done")))
    monkeypatch.setattr(NotificationPreferenceService, "should_notify", AsyncMock(return_value=True))
    deliver = AsyncMock(return_value={"status": "delivered", "target": task.delivery, "surface": "channel"})
    monkeypatch.setattr(ScheduledTaskService, "_deliver_run_notification", deliver)
    _patch_delivery_chain(monkeypatch)

    run = await ScheduledTaskService._execute_and_finalize(db, task, _run_record())

    deliver.assert_awaited_once_with(task, run)
    assert run.delivery_result["status"] == "delivered"


@pytest.mark.asyncio
async def test_failed_run_redacts_internal_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _mock_db()
    monkeypatch.setattr(ScheduledTaskService, "_execute_task", AsyncMock(side_effect=RuntimeError("password=must-not-leak stack /private/path")))
    run = await ScheduledTaskService._execute_and_finalize(db, _task(), _run_record())
    assert run.status == "failed"
    assert run.error_category == "execution"
    assert "must-not-leak" not in run.error_message
    assert "/private/path" not in run.error_message


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_returns_existing_run(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = TUserScheduledTaskRun(id="run-1", task_id="task-1", user_id=1, status="failed", trigger_source="retry", retry_of="old", idempotency_key="retry-key", created_at=1)
    db = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult(one=existing)), add=lambda _row: pytest.fail("must not insert"))
    monkeypatch.setattr(ScheduledTaskService, "_execute_task", AsyncMock())
    result = await ScheduledTaskService._create_run_record(db, _task(), trigger_source="retry", idempotency_key="retry-key", retry_of="old")
    assert result is existing
    ScheduledTaskService._execute_task.assert_not_awaited()


# ---------------------------------------------------------------------------
# 交付链收口等待（_await_session_delivery）
# ---------------------------------------------------------------------------

def _patch_session_state(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tasks_by_poll: list[list[dict]] | None = None,
    pending_wake: bool = False,
):
    """注入会话状态：executor 任务快照（按轮询次序）、待发唤醒、活跃 run。"""
    from noesis.agents.background.executor import BackgroundTaskExecutor
    from noesis.repositories.agent_run_repository import AgentRunRepository
    import noesis.services.bg_continuation_service as continuation

    snapshots = list(tasks_by_poll or [[]])
    calls = {"n": 0}

    def fake_list(session_id):
        i = min(calls["n"], len(snapshots) - 1)
        calls["n"] += 1
        return snapshots[i] if i < len(snapshots) else snapshots[-1]

    monkeypatch.setattr(BackgroundTaskExecutor, "list_for_session", staticmethod(fake_list))
    monkeypatch.setattr(continuation, "has_pending_wake", lambda session_id: pending_wake)
    monkeypatch.setattr(
        AgentRunRepository, "get_active_for_session", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(ScheduledTaskService, "_DELIVERY_POLL_SECONDS", 0.01)
    return calls


@pytest.mark.asyncio
async def test_delivery_wait_blocks_on_running_bg_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """后台任务在跑：不收口；任务全部终态后按双观测确认空闲。"""
    calls = _patch_session_state(
        monkeypatch,
        tasks_by_poll=[[{"status": "running"}], [{"status": "completed"}], [{"status": "completed"}]],
    )
    assert await ScheduledTaskService._await_session_delivery("1", "session-1") is True
    # 会话曾有任务：空闲需连续两次观测（completed 出现两轮后才返回）
    assert calls["n"] >= 3


@pytest.mark.asyncio
async def test_delivery_wait_immediate_when_no_bg_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    """主 run 未派生后台任务：单次空闲观测即收口（不加无谓延迟）。"""
    calls = _patch_session_state(monkeypatch, tasks_by_poll=[[]])
    assert await ScheduledTaskService._await_session_delivery("1", "session-1") is True
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_delivery_wait_blocks_on_pending_wake(monkeypatch: pytest.MonkeyPatch) -> None:
    """去抖窗口内待发续跑唤醒：交付链未收口，不返回。"""
    calls = _patch_session_state(monkeypatch, tasks_by_poll=[[]], pending_wake=True)
    monkeypatch.setattr(ScheduledTaskService, "_DELIVERY_TIMEOUT_SECONDS", 0.05)
    assert await ScheduledTaskService._await_session_delivery("1", "session-1") is False
    assert calls["n"] >= 1


@pytest.mark.asyncio
async def test_delivery_wait_timeout_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """长期不收口：超时返回 False（调用方按 delivery_timeout 落终态，防 watcher 泄漏）。"""
    _patch_session_state(monkeypatch, tasks_by_poll=[[{"status": "running"}]])
    monkeypatch.setattr(ScheduledTaskService, "_DELIVERY_TIMEOUT_SECONDS", 0.05)
    assert await ScheduledTaskService._await_session_delivery("1", "session-1") is False


@pytest.mark.asyncio
async def test_delivery_wait_blocks_on_active_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """continuation run 仍在执行：不收口。"""
    from noesis.repositories.agent_run_repository import AgentRunRepository

    _patch_session_state(monkeypatch, tasks_by_poll=[[{"status": "completed"}]])
    monkeypatch.setattr(
        AgentRunRepository, "get_active_for_session", AsyncMock(return_value=SimpleNamespace(id="run-x"))
    )
    monkeypatch.setattr(ScheduledTaskService, "_DELIVERY_TIMEOUT_SECONDS", 0.05)
    assert await ScheduledTaskService._await_session_delivery("1", "session-1") is False


# ---------------------------------------------------------------------------
# tick 异步化（调度器不 await 执行体）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tick_spawns_execution_without_awaiting(monkeypatch: pytest.MonkeyPatch) -> None:
    from noesis.services import scheduled_task_scheduler as scheduler

    task_row = _task()
    run = _run_record()
    started = asyncio.Event()

    async def fake_claim(db, *, limit=20):
        return [task_row]

    async def fake_create(db, row, *, trigger_source, idempotency_key, retry_of=None):
        return run

    async def fake_background(task_id, user_id, run_id):
        started.set()
        await asyncio.sleep(0.25)

    async def fake_cleanup(db, user_id, **kw):
        return 0

    monkeypatch.setattr(ScheduledTaskService, "claim_due_tasks", fake_claim)
    monkeypatch.setattr(ScheduledTaskService, "_create_run_record", fake_create)
    monkeypatch.setattr(ScheduledTaskService, "_run_in_background", fake_background)
    monkeypatch.setattr(ScheduledTaskService, "cleanup_runs", fake_cleanup)

    class _Ctx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(scheduler.pg_manager, "get_async_session_context", lambda: _Ctx())

    t0 = time.monotonic()
    await scheduler._tick_once()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.15, f"tick 不得 await 执行体（耗时 {elapsed:.2f}s）"
    await asyncio.sleep(0)  # 让出一个 loop 轮次，派发的后台任务进入运行
    assert started.is_set(), "执行体应已后台派发"
    # 等待派发的后台任务结束，避免测试事件循环残留告警
    await asyncio.sleep(0.3)


@pytest.mark.asyncio
async def test_run_in_background_skips_terminal_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """已终态（含 interrupted）的 run 不重复执行——重启对账后幂等。"""
    from noesis.services.scheduled_task_service import ScheduledTaskService as Svc

    executed = AsyncMock()
    monkeypatch.setattr(Svc, "_execute_and_finalize", executed)

    class _Exec:
        def __init__(self, one):
            self._one = one

        def scalar_one_or_none(self):
            return self._one

    class _DB:
        def __init__(self, task, run):
            self._task, self._run = task, run
            self.calls = 0

        async def execute(self, *_a, **_k):
            self.calls += 1
            return _Exec(self._task if self.calls == 1 else self._run)

    class _Ctx:
        def __init__(self, db):
            self._db = db

        async def __aenter__(self):
            return self._db

        async def __aexit__(self, *a):
            return False

    from noesis.storage.postgres.manager import pg_manager

    terminal_run = SimpleNamespace(id="run-1", status="interrupted")
    db = _DB(_task(), terminal_run)
    monkeypatch.setattr(pg_manager, "get_async_session_context", lambda: _Ctx(db))
    await Svc._run_in_background("task-1", 1, "run-1")
    executed.assert_not_awaited()


@pytest.mark.asyncio
async def test_due_task_claim_uses_skip_locked() -> None:
    db = _mock_db()
    assert await ScheduledTaskService.claim_due_tasks(db) == []
    statement = str(db.execute.await_args.args[0])
    assert "FOR UPDATE" in statement


@pytest.mark.asyncio
async def test_run_detail_lookup_is_scoped_by_user() -> None:
    db = _mock_db()
    assert await ScheduledTaskService.get_run(db, 42, "other-user-run") is None
    statement = str(db.execute.await_args.args[0])
    assert "user_scheduled_task_runs.user_id" in statement
