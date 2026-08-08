"""自动化运行状态、幂等重试与调度边界。"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from noesis_server.models.settings_models import TUserScheduledTaskRun
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


def test_cron_preview_and_dst_boundary() -> None:
    assert cron_summary("0 9 * * *", "Asia/Shanghai") == "每天 09:00（Asia/Shanghai）"
    before_dst = int(datetime(2026, 3, 7, 12, tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000)
    next_ms = compute_next_run_ms("30 2 * * *", "America/New_York", after_ms=before_dst)
    next_local = datetime.fromtimestamp(next_ms / 1000, ZoneInfo("America/New_York"))
    assert next_ms > before_dst
    assert next_local.date().isoformat() in {"2026-03-08", "2026-03-09"}


@pytest.mark.asyncio
async def test_successful_run_has_terminal_record(monkeypatch: pytest.MonkeyPatch) -> None:
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarResult()), add=lambda row: captured.append(row),
        commit=AsyncMock(), refresh=AsyncMock(),
    )
    captured = []
    monkeypatch.setattr(ScheduledTaskService, "_execute_task", AsyncMock(return_value=SimpleNamespace(session_id="session-1", plain_text="done")))
    run = await ScheduledTaskService.execute_with_record(db, _task(), trigger_source="manual", idempotency_key="manual-1")
    assert run.status == "succeeded"
    assert run.started_at and run.finished_at
    assert run.session_id == "session-1"
    assert run.result_summary == "done"
    assert captured == [run]


@pytest.mark.asyncio
async def test_notification_preference_does_not_stop_business_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from noesis.services.notification_preference_service import NotificationPreferenceService
    task = _task()
    task.delivery = "web_notify"
    db = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult()), add=lambda _row: None, commit=AsyncMock(), refresh=AsyncMock())
    execute = AsyncMock(return_value=SimpleNamespace(session_id="session-1", plain_text="done"))
    monkeypatch.setattr(ScheduledTaskService, "_execute_task", execute)
    monkeypatch.setattr(NotificationPreferenceService, "should_notify", AsyncMock(return_value=False))

    run = await ScheduledTaskService.execute_with_record(db, task, trigger_source="manual", idempotency_key="manual-suppressed")

    execute.assert_awaited_once()
    assert run.status == "succeeded"
    assert run.delivery_result["status"] == "suppressed"


@pytest.mark.asyncio
async def test_enabled_web_notification_is_delivered_via_run_record(monkeypatch: pytest.MonkeyPatch) -> None:
    from noesis.services.notification_preference_service import NotificationPreferenceService

    task = _task()
    task.delivery = "web_notify"
    db = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult()), add=lambda _row: None, commit=AsyncMock(), refresh=AsyncMock())
    monkeypatch.setattr(ScheduledTaskService, "_execute_task", AsyncMock(return_value=SimpleNamespace(session_id="session-1", plain_text="done")))
    monkeypatch.setattr(NotificationPreferenceService, "should_notify", AsyncMock(return_value=True))

    run = await ScheduledTaskService.execute_with_record(db, task, trigger_source="manual", idempotency_key="manual-delivered")

    assert run.delivery_result == {"status": "delivered", "target": "web_notify", "surface": "web"}


@pytest.mark.asyncio
async def test_enabled_channel_notification_uses_delivery_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    from noesis.services.notification_preference_service import NotificationPreferenceService

    task = _task()
    task.delivery = "channel:channel-1"
    db = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult()), add=lambda _row: None, commit=AsyncMock(), refresh=AsyncMock())
    monkeypatch.setattr(ScheduledTaskService, "_execute_task", AsyncMock(return_value=SimpleNamespace(session_id="session-1", plain_text="done")))
    monkeypatch.setattr(NotificationPreferenceService, "should_notify", AsyncMock(return_value=True))
    deliver = AsyncMock(return_value={"status": "delivered", "target": task.delivery, "surface": "channel"})
    monkeypatch.setattr(ScheduledTaskService, "_deliver_run_notification", deliver)

    run = await ScheduledTaskService.execute_with_record(db, task, trigger_source="manual", idempotency_key="channel-delivered")

    deliver.assert_awaited_once_with(task, run)
    assert run.delivery_result["status"] == "delivered"


@pytest.mark.asyncio
async def test_failed_run_redacts_internal_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    db = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult()), add=lambda _row: None, commit=AsyncMock(), refresh=AsyncMock())
    monkeypatch.setattr(ScheduledTaskService, "_execute_task", AsyncMock(side_effect=RuntimeError("password=must-not-leak stack /private/path")))
    run = await ScheduledTaskService.execute_with_record(db, _task(), trigger_source="schedule", idempotency_key="schedule-1")
    assert run.status == "failed"
    assert run.error_category == "execution"
    assert "must-not-leak" not in run.error_message
    assert "/private/path" not in run.error_message


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_returns_existing_run(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = TUserScheduledTaskRun(id="run-1", task_id="task-1", user_id=1, status="failed", trigger_source="retry", retry_of="old", idempotency_key="retry-key", created_at=1)
    db = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult(one=existing)), add=lambda _row: pytest.fail("must not insert"))
    monkeypatch.setattr(ScheduledTaskService, "_execute_task", AsyncMock())
    result = await ScheduledTaskService.execute_with_record(db, _task(), trigger_source="retry", idempotency_key="retry-key", retry_of="old")
    assert result is existing
    ScheduledTaskService._execute_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_due_task_claim_uses_skip_locked() -> None:
    db = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult(many=[])))
    assert await ScheduledTaskService.claim_due_tasks(db) == []
    statement = str(db.execute.await_args.args[0])
    assert "FOR UPDATE" in statement


@pytest.mark.asyncio
async def test_run_detail_lookup_is_scoped_by_user() -> None:
    db = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult()))
    assert await ScheduledTaskService.get_run(db, 42, "other-user-run") is None
    statement = str(db.execute.await_args.args[0])
    assert "user_scheduled_task_runs.user_id" in statement
