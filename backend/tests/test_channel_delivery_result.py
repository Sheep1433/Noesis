from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from noesis.services.channel_run_service import _set_delivery_result
from noesis.chat.delivery.events import RunCompleted
from noesis.chat.runs import RunStatus, TerminalCommitResult
from noesis.services import channel_run_service as channel_service


class _DbContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_channel_delivery_failure_is_written_independently(monkeypatch) -> None:
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    monkeypatch.setattr(
        "noesis.storage.postgres.manager.pg_manager.get_async_session_context",
        lambda: _DbContext(db),
    )

    await _set_delivery_result(
        "delivery-1", "error", error_code="CHANNEL_SEND_FAILED"
    )

    db.execute.assert_awaited_once()
    db.commit.assert_awaited_once()
    statement = db.execute.await_args.args[0]
    params = statement.compile().params
    assert "error" in params.values()
    assert "CHANNEL_SEND_FAILED" in params.values()


@pytest.mark.asyncio
async def test_no_outbound_delivery_does_not_write(monkeypatch) -> None:
    local = MagicMock()
    monkeypatch.setattr(
        "noesis.storage.postgres.manager.pg_manager.get_async_session_context", local
    )

    await _set_delivery_result(None, "completed")

    local.assert_not_called()


@pytest.mark.asyncio
async def test_headless_automation_run_completes_without_browser_subscription(monkeypatch) -> None:
    db = MagicMock()
    db.add = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    monkeypatch.setattr("noesis.storage.postgres.manager.pg_manager.get_async_session_context", lambda: _DbContext(db))
    monkeypatch.setattr(
        channel_service.UserService,
        "get_user_by_id",
        AsyncMock(return_value=SimpleNamespace(user_id="user-1")),
    )
    monkeypatch.setattr(channel_service.ChatService, "get_or_create_session", AsyncMock())
    monkeypatch.setattr(channel_service.ChatService, "save_message", AsyncMock())
    from noesis.services.qa import helpers as qs

    monkeypatch.setattr(qs, "_resolve_model_for_query", AsyncMock(return_value=None))
    monkeypatch.setattr(qs, "_resolved_model_name", lambda _model_id: None)
    monkeypatch.setattr(qs, "_resolve_mcp_servers_for_query", AsyncMock(return_value=[]))
    monkeypatch.setattr(qs, "_resolve_enabled_skills_for_query", AsyncMock(return_value=[]))
    monkeypatch.setattr(qs, "_insert_streaming_assistant_skeleton", AsyncMock(return_value=True))
    monkeypatch.setattr(channel_service._super_agent, "run_agent", MagicMock(return_value=object()))

    async def fake_headless_stream(**kwargs):
        event = RunCompleted(finish_reason="stop")
        await kwargs["publish"](event)
        return channel_service.ChannelRunResult(
            session_id=kwargs["session_id"],
            assistant_message_id=kwargs["bridge"].assistant_message_id,
            plain_text="完成",
            finish_reason="stop",
        )

    monkeypatch.setattr(channel_service, "_headless_stream", fake_headless_stream)
    monkeypatch.setattr(
        channel_service.RunService,
        "_persist_terminal_candidate",
        AsyncMock(return_value=TerminalCommitResult("committed")),
    )

    result = await channel_service.run_channel_agent(
        user_id="1",
        session_id="session-1",
        query="执行自动化任务",
        origin="automation",
        channel_type="automation",
    )

    assert result.finish_reason == "stop"
    run_rows = [call.args[0] for call in db.add.call_args_list if hasattr(call.args[0], "origin")]
    assert run_rows[0].origin == "automation"
    handle = channel_service.run_manager.get(run_rows[0].id)
    assert handle.status == RunStatus.COMPLETED
    assert not handle.subscribers
    await channel_service.run_manager.remove_terminal(handle.run_id)
