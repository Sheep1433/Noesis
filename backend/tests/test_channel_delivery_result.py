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


@pytest.mark.asyncio
async def test_channel_run_start_mark_is_cas_not_overwrite(monkeypatch) -> None:
    """竞态回归：producer 毫秒级失败先落终态时，启跑标记必须是 queued→running
    的 CAS。无条件覆写终态 run 会制造「消息已终态而 run 遗留非终态」的毒丸
    （automation run 3a49aac1 即此产物，炸下次启动对账）。"""
    from noesis.chat.delivery.events import RunError

    db = MagicMock()
    db.add = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    monkeypatch.setattr(
        "noesis.storage.postgres.manager.pg_manager.get_async_session_context",
        lambda: _DbContext(db),
    )
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

    cas_calls: list = []

    async def spy_cas(self, run_id, expected, target, **values):
        cas_calls.append((run_id, list(expected), target))
        return True

    monkeypatch.setattr(
        channel_service.AgentRunRepository, "compare_and_set_status", spy_cas
    )

    async def fast_fail_stream(**kwargs):
        # 模型毫秒级失败：终态事件先于主协程的启跑标记到达
        await kwargs["publish"](
            RunError(message="操作失败，请稍后重试。", finish_reason="error")
        )
        return channel_service.ChannelRunResult(
            session_id=kwargs["session_id"],
            assistant_message_id=kwargs["bridge"].assistant_message_id,
            plain_text="",
            finish_reason="error",
        )

    monkeypatch.setattr(channel_service, "_headless_stream", fast_fail_stream)
    monkeypatch.setattr(
        channel_service.RunService,
        "_persist_terminal_candidate",
        AsyncMock(return_value=TerminalCommitResult("committed")),
    )

    result = await channel_service.run_channel_agent(
        user_id="1",
        session_id="session-race",
        query="自动任务",
        origin="automation",
        channel_type="automation",
    )

    assert result.finish_reason == "error"
    # 启跑标记必须经 CAS 且前置仅接受 queued：终态 run 不被覆写回 running
    assert cas_calls, "启跑标记必须走 compare_and_set_status（无条件 UPDATE 已退役）"
    assert cas_calls[0][1] == [RunStatus.QUEUED]
    assert cas_calls[0][2] is RunStatus.RUNNING
    run_rows = [
        call.args[0] for call in db.add.call_args_list if hasattr(call.args[0], "origin")
    ]
    handle = channel_service.run_manager.get(run_rows[0].id)
    assert handle.status == RunStatus.ERROR
    await channel_service.run_manager.remove_terminal(handle.run_id)
