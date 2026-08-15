import json
import asyncio
from dataclasses import replace
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError

from server.api import chat_api
from server.api.chat_api import chat_router
from noesis.chat.delivery.events import HitlRequired, RunCompleted, StreamDone, WireFrame
from noesis.chat.delivery.sse import encode_sequenced_event
from noesis.chat.runs import (
    RunManager,
    RunSnapshot,
    RunStatus,
    SequencedRunEvent,
    TerminalCandidate,
)
from noesis.errors.exceptions import ConflictException, ServiceException
from noesis.schemas.chat_vo import CreateRunRequest
from noesis.schemas.qa_vo import HitlResumeRequest


async def _noop(*args: Any, **kwargs: Any) -> None:
    """空 async producer，用于不需要发事件的 Run 启动。"""
    return None


from noesis.services import run_service
from noesis.services.run_service import RunProjection, RunService


def test_run_routes_replace_legacy_stream_and_stop() -> None:
    paths = {(route.path, method) for route in chat_router.routes for method in route.methods}
    assert ("/api/chat/runs", "POST") in paths
    assert ("/api/chat/runs/{run_id}", "GET") in paths
    assert ("/api/chat/runs/{run_id}/stream", "GET") in paths
    assert ("/api/chat/runs/{run_id}/stop", "POST") in paths
    assert ("/api/chat/runs/{run_id}/hitl/resume", "POST") in paths
    assert ("/api/chat/runs/{run_id}/test-case/resume", "POST") in paths
    assert ("/api/chat/sessions/stream", "POST") not in paths
    assert ("/api/chat/sessions/{session_id}/stop", "POST") not in paths
    assert ("/api/chat/sessions/{session_id}/test-case/resume", "POST") not in paths
    assert ("/api/chat/sessions/{session_id}/hitl/resume", "POST") not in paths


@pytest.mark.asyncio
async def test_run_stream_consumes_stream_done_without_crashing(monkeypatch) -> None:
    """真实迭代 StreamingResponse generator，防止路由存在但首帧运行时报 NameError。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    snapshot = RunSnapshot(
        run_id="run-stream",
        user_id="1",
        session_id="session-1",
        assistant_message_id="assistant-1",
        qa_type="SUPER_AGENT_QA",
        origin="web",
        status=RunStatus.RUNNING,
    )
    queue: asyncio.Queue = asyncio.Queue()
    await queue.put(
        SequencedRunEvent(
            run_id="run-stream",
            sequence=1,
            attempt_id=1,
            event=StreamDone(),
        )
    )
    subscription = SimpleNamespace(snapshot=snapshot, replay=(), queue=queue)
    monkeypatch.setattr(chat_api.RunService, "get", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(
        chat_api.RunService,
        "subscribe",
        AsyncMock(return_value=subscription),
    )
    unsubscribe = AsyncMock()
    monkeypatch.setattr(chat_api.run_manager, "unsubscribe", unsubscribe)

    response = await chat_api.stream_run(
        "run-stream",
        current_user=SimpleNamespace(user_id=1),
        db=SimpleNamespace(),
    )
    chunks = [chunk async for chunk in response.body_iterator]

    assert any("run-snapshot" in chunk for chunk in chunks)
    assert any("[DONE]" in chunk for chunk in chunks)
    unsubscribe.assert_awaited_once_with("run-stream", queue)


@pytest.mark.asyncio
async def test_active_run_route_returns_explicit_null(monkeypatch) -> None:
    """无活跃任务时响应必须显式包含 data=null。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    monkeypatch.setattr(chat_api.RunService, "get_active_run", AsyncMock(return_value=None))
    response = await chat_api.get_active_run(
        "session-1",
        current_user=SimpleNamespace(user_id="user-1"),
        db=SimpleNamespace(),
    )
    body = json.loads(response.body.decode())

    assert response.status_code == 200
    assert "data" in body
    assert body["data"] is None


@pytest.mark.asyncio
async def test_run_stream_returns_429_before_opening_stream(monkeypatch) -> None:
    """订阅配额超限时不得建立 StreamingResponse。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    snapshot = RunSnapshot(
        run_id="run-quota",
        user_id="user-1",
        session_id="session-1",
        assistant_message_id="assistant-1",
        qa_type="COMMON_QA",
        origin="web",
        status=RunStatus.RUNNING,
    )
    monkeypatch.setattr(chat_api.RunService, "get", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(
        chat_api.RunService,
        "subscribe",
        AsyncMock(side_effect=chat_api.SubscriptionLimitExceeded()),
    )

    response = await chat_api.stream_run(
        "run-quota",
        current_user=SimpleNamespace(user_id="user-1"),
        db=SimpleNamespace(),
    )
    body = json.loads(response.body.decode())

    assert response.status_code == 429
    assert body["data"]["error_code"] == "SSE_SUBSCRIPTION_LIMIT"


@pytest.mark.asyncio
async def test_run_stream_returns_503_when_owner_is_unavailable(monkeypatch) -> None:
    """非终态 DB Run 无本地 owner 时不得创建第二 producer。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    snapshot = RunSnapshot(
        run_id="run-orphan",
        user_id="user-1",
        session_id="session-1",
        assistant_message_id="assistant-1",
        qa_type="COMMON_QA",
        origin="web",
        status=RunStatus.RUNNING,
        sequence=7,
    )
    monkeypatch.setattr(chat_api.RunService, "get", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(chat_api.RunService, "subscribe", AsyncMock(return_value=None))

    response = await chat_api.stream_run(
        "run-orphan",
        current_user=SimpleNamespace(user_id="user-1"),
        db=SimpleNamespace(),
    )
    body = json.loads(response.body.decode())

    assert response.status_code == 503
    assert body["data"]["error_code"] == "RUN_OWNER_UNAVAILABLE"
    assert body["data"]["sequence"] == 7


@pytest.mark.asyncio
async def test_terminal_projection_atomically_finalizes_run_and_assistant(monkeypatch) -> None:
    """自然完成不能只终结 t_agent_run 而把 assistant 永久留在 streaming。"""
    from unittest.mock import AsyncMock, MagicMock

    projection = RunProjection(
        run_id="run-terminal",
        user_id="1",
        session_id="session-1",
        assistant_message_id="assistant-1",
        qa_type="SUPER_AGENT_QA",
    )
    projection.apply(WireFrame(event="text-delta", data={"text_delta": "完成"}))
    projection.apply(RunCompleted(finish_reason="stop"))
    envelope = SequencedRunEvent(
        run_id="run-terminal",
        sequence=7,
        attempt_id=1,
        event=RunCompleted(finish_reason="stop"),
    )
    candidate = TerminalCandidate(
        envelope=envelope,
        snapshot=projection.snapshot(7, RunStatus.COMPLETED, 1),
        status=RunStatus.COMPLETED,
        projected_state=projection,
    )

    db = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    class DbContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    repository = MagicMock()
    repository.finalize = AsyncMock(return_value=True)
    monkeypatch.setattr("noesis.storage.postgres.manager.pg_manager.get_async_session_context", DbContext)
    monkeypatch.setattr(run_service, "AgentRunRepository", lambda _db: repository)
    result = await RunService._persist_terminal_candidate(candidate)

    kwargs = repository.finalize.await_args.kwargs
    assert kwargs["target"] == RunStatus.COMPLETED
    assert kwargs["last_sequence"] == 7
    assert kwargs["assistant_status"] == "completed"
    assert kwargs["content"] == {"parts": [{"type": "text", "content": "完成"}]}
    db.commit.assert_awaited_once()
    assert result.outcome == "committed"


def test_hitl_payload_is_present_in_authoritative_snapshot() -> None:
    projection = RunProjection(
        run_id="run-hitl",
        user_id="1",
        session_id="session-1",
        assistant_message_id="assistant-1",
        qa_type="SUPER_AGENT_QA",
    )
    projection.apply(
        WireFrame(
            event="tool-call-start",
            data={
                "tool_name": "execute",
                "tool_call_id": "call-curl",
                "input": {"command": "curl https://example.com"},
            },
        )
    )
    payload = {
        "interrupt_id": "interrupt-1",
        "kind": "approval",
        "action_requests": [
            {"tool_call_id": "call-curl", "name": "execute", "args": {}}
        ],
        "review_configs": [],
        "expires_at": 123,
    }
    projection.apply(HitlRequired(payload=payload))

    snapshot = projection.snapshot(2, RunStatus.HITL_PENDING, 1)
    assert snapshot.status == RunStatus.HITL_PENDING
    assert snapshot.pending_hitl == payload
    tool = snapshot.parts[0]
    assert tool["hitl"] == {
        "kind": "approval",
        "status": "pending",
        "interrupt_id": "interrupt-1",
    }
    assert projection.persisted_snapshot()["_pending_hitl"] == payload

    projection.begin_hitl_resume()
    resumed = projection.snapshot(2, RunStatus.RUNNING, 1)
    assert resumed.status == RunStatus.RUNNING
    assert resumed.pending_hitl is None
    assert "_pending_hitl" not in projection.persisted_snapshot()


def test_hitl_decision_updates_authoritative_tool_part_before_resume() -> None:
    projection = RunProjection(
        run_id="run-hitl-decision",
        user_id="1",
        session_id="session-1",
        assistant_message_id="assistant-1",
        qa_type="SUPER_AGENT_QA",
    )
    projection.apply(WireFrame(event="tool-call-start", data={
        "tool_name": "execute",
        "tool_call_id": "call-1",
        "input": {"command": "curl https://example.com"},
    }))
    projection.apply(HitlRequired(payload={
        "interrupt_id": "interrupt-1",
        "kind": "approval",
        "action_requests": [{"tool_call_id": "call-1", "name": "execute", "args": {}}],
    }))

    projection.apply_hitl_decisions([{"type": "approve"}])
    projection.begin_hitl_resume()

    tool = projection.snapshot(2, RunStatus.RUNNING, 1).parts[0]
    assert tool["hitl"]["status"] == "approved"
    assert tool["hitl"]["decision"] == "approve"
    assert tool["hitl"]["interrupt_id"] == "interrupt-1"


@pytest.mark.asyncio
async def test_hitl_segment_done_does_not_close_run_subscription() -> None:
    """HITL 分段的 [DONE] 不得进入 Run event bus，resume 后沿用原订阅。

    新架构下 StreamDone+HITL_PENDING 检查在 RunProjection.apply 内（返回 False），
    apply_event 据此返回 None 且不分配 sequence。用真实 RunManager 验证。
    """
    from noesis.chat.runs import RunManager, RunStatus

    projection = RunProjection(
        run_id="run-hitl-stream",
        user_id="1",
        session_id="session-1",
        assistant_message_id="assistant-1",
        qa_type="SUPER_AGENT_QA",
    )
    manager = RunManager()
    handle = await manager.start(
        run_id="run-hitl-stream",
        session_id="session-1",
        user_id="1",
        assistant_message_id="assistant-1",
        snapshot_provider=projection.snapshot,
        producer=lambda publish: _noop(),
        state=projection,
    )
    await handle.producer_task

    # 先进入 HITL_PENDING（用 apply_event 让 projection.apply 执行）
    await manager.apply_event(
        "run-hitl-stream",
        HitlRequired(payload={"interrupt_id": "interrupt-1"}),
        producer_generation=handle.producer_generation,
    )
    assert projection.status == RunStatus.HITL_PENDING

    # StreamDone 在 HITL_PENDING 时应被 projection.apply 拒绝（返回 None）
    envelope = await manager.apply_event(
        "run-hitl-stream", StreamDone(), producer_generation=handle.producer_generation
    )
    assert envelope is None
    # sequence 未推进
    assert handle.last_sequence == 1


def test_sequenced_sse_contains_run_identity() -> None:
    envelope = SequencedRunEvent(
        run_id="run-1",
        sequence=9,
        attempt_id=2,
        event=WireFrame(event="text-delta", data={"type": "text-delta", "delta": "hi"}),
    )
    line = encode_sequenced_event(envelope)[0]
    payload = json.loads(next(part[5:].strip() for part in line.splitlines() if part.startswith("data:")))
    assert payload["run_id"] == "run-1"
    assert payload["sequence"] == 9
    assert payload["attempt_id"] == 2


@pytest.mark.asyncio
async def test_resume_hitl_uses_projection_pending_without_legacy_store(monkeypatch) -> None:
    """网页 Run resume 以 projection 为权威，不依赖进程内 legacy pending store。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    projection = RunProjection(
        run_id="run-hitl",
        user_id="1",
        session_id="session-1",
        assistant_message_id="assistant-1",
        qa_type="SUPER_AGENT_QA",
    )
    projection.apply(HitlRequired(payload={
        "interrupt_id": "interrupt-1",
        "kind": "approval",
        "action_requests": [{"tool_call_id": "call-1", "name": "execute", "args": {}}],
        "review_configs": [],
        "expires_at": 9999999999,
    }))
    handle = SimpleNamespace(
        state=projection,
        last_sequence=3,
        status=RunStatus.HITL_PENDING,
        attempt_id=1,
        snapshot_provider=projection.snapshot,
    )
    row = SimpleNamespace(
        id="run-hitl",
        user_id="1",
        session_id="session-1",
        assistant_message_id="assistant-1",
        status=RunStatus.HITL_PENDING.value,
    )
    repository = MagicMock()
    repository.get = AsyncMock(return_value=row)
    repository.compare_and_set_status = AsyncMock(return_value=True)
    monkeypatch.setattr(run_service, "AgentRunRepository", lambda _db: repository)
    monkeypatch.setattr(run_service.run_manager, "get", MagicMock(return_value=handle))

    captured = {}

    async def resume(_run_id, producer, *, prepare=None):
        if prepare is not None:
            prepare()
        async def publish(_event, _attempt_id):
            return SimpleNamespace(sequence=4)
        await producer(publish)
        handle.status = RunStatus.RUNNING
        return handle

    monkeypatch.setattr(run_service.run_manager, "resume", resume)
    monkeypatch.setattr(RunService, "_persist_projection", AsyncMock())

    async def exec_resume(**kwargs):
        captured.update(kwargs)
        if False:
            yield ""

    monkeypatch.setattr(run_service.QaService, "exec_hitl_resume", exec_resume)

    run_db = MagicMock()
    class DbContext:
        async def __aenter__(self):
            return run_db
        async def __aexit__(self, *_args):
            return False
    monkeypatch.setattr("noesis.storage.postgres.manager.pg_manager.get_async_session_context", DbContext)

    db = MagicMock()
    db.commit = AsyncMock()
    request = HitlResumeRequest(
        interrupt_id="interrupt-1",
        decisions=[{"type": "approve"}],
        grant_scope="once",
    )
    current_user = SimpleNamespace(user_id=1)

    snapshot = await RunService.resume_hitl("run-hitl", request, current_user, db)

    assert captured["pending"].interrupt_id == "interrupt-1"
    assert captured["pending"].assistant_message_id == "assistant-1"
    assert "run_managed" not in captured
    assert snapshot.status == RunStatus.RUNNING
    repository.compare_and_set_status.assert_awaited_once()


def test_run_projection_builds_authoritative_text_snapshot() -> None:
    projection = RunProjection(
        run_id="run-1",
        user_id="user-1",
        session_id="session-1",
        assistant_message_id="message-1",
        qa_type="COMMON_QA",
    )
    projection.apply(WireFrame(event="text-delta", data={"delta": "你"}))
    projection.apply(WireFrame(event="text-delta", data={"delta": "好"}))
    snapshot = projection.snapshot(2, projection.status, 1)
    assert snapshot.parts == ({"type": "text", "content": "你好"},)


def test_run_projection_accepts_current_bridge_field_names() -> None:
    projection = RunProjection(
        run_id="run-current-wire",
        user_id="user-1",
        session_id="session-1",
        assistant_message_id="message-1",
        qa_type="COMMON_QA",
    )
    projection.apply(WireFrame(event="text-delta", data={"text_delta": "正文"}))
    projection.apply(
        WireFrame(
            event="tool-input-available",
            data={"name": "lookup", "tool_call_id": "call-1", "input": {"q": "x"}},
        )
    )

    parts = projection.builder.to_dict()["parts"]
    assert parts[0]["content"] == "正文"
    assert parts[1]["name"] == "lookup"


def test_run_projection_discards_late_tool_result_after_cancel() -> None:
    projection = RunProjection(
        run_id="run-1",
        user_id="user-1",
        session_id="session-1",
        assistant_message_id="message-1",
        qa_type="COMMON_QA",
    )
    projection.apply(
        WireFrame(
            event="tool-call-start",
            data={"tool_name": "remote_write", "tool_call_id": "call-1", "input": {}},
        )
    )
    projection.cancel_requested = True
    projection.builder.mark_running_tools_unknown("执行结果无法确认")
    projection.apply(
        WireFrame(
            event="tool-output-available",
            data={"tool_call_id": "call-1", "output": "late success", "status": "success"},
        )
    )
    part = projection.builder.to_dict()["parts"][0]
    assert part["outcome"] == "unknown"
    assert part["status"] == "error"


@pytest.mark.parametrize(
    "qa_type",
    ["COMMON_QA", "SUPER_AGENT_QA", "FAULT_OPERATION_QA", "TEST_CASE_QA"],
)
def test_all_qa_types_keep_run_and_assistant_identity(qa_type: str) -> None:
    request = CreateRunRequest(
        session_id="session-1",
        content="question",
        client_request_id=f"request-{qa_type}",
        extra={"qa_type": qa_type},
    )
    qa_request = RunService._to_qa_request(request, qa_type)
    projection = RunProjection(
        run_id="run-1",
        user_id="user-1",
        session_id="session-1",
        assistant_message_id="message-1",
        qa_type=qa_type,
    )
    projection.apply(WireFrame(event="text-delta", data={"delta": "ok"}))
    snapshot = projection.snapshot(1, RunStatus.RUNNING, 1)

    assert qa_request.qa_type == qa_type
    assert snapshot.run_id == "run-1"
    assert snapshot.assistant_message_id == "message-1"
    assert snapshot.qa_type == qa_type


def test_projection_ignores_old_attempt_delta() -> None:
    projection = RunProjection(
        run_id="run-1",
        user_id="user-1",
        session_id="session-1",
        assistant_message_id="message-1",
        qa_type="COMMON_QA",
        attempt_id=2,
    )
    accepted = projection.apply(
        WireFrame(event="text-delta", data={"delta": "late"}), attempt_id=1
    )
    assert accepted is False
    assert projection.builder.to_dict()["parts"] == []


@pytest.mark.asyncio
async def test_checkpoint_db_outage_fails_within_configured_deadline(monkeypatch) -> None:
    attempts = 0

    class FailingSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, _statement):
            nonlocal attempts
            attempts += 1
            raise ConnectionError("database unavailable")

    snapshot = RunSnapshot(
        run_id="run-db-fail",
        user_id="user-1",
        session_id="session-1",
        assistant_message_id="message-1",
        qa_type="COMMON_QA",
        origin="web",
        status=RunStatus.RUNNING,
        sequence=1,
        attempt_id=1,
    )
    monkeypatch.setattr("noesis.storage.postgres.manager.pg_manager.get_async_session_context", FailingSession)
    monkeypatch.setattr(
        run_service,
        "StreamConfig",
        replace(
            run_service.StreamConfig,
            persistence_timeout_seconds=0.01,
            persistence_retry_interval_seconds=0.001,
        ),
    )

    with pytest.raises(RuntimeError, match="checkpoint persistence timeout"):
        await RunService._persist_checkpoint("run-db-fail", "message-1", snapshot, 1)

    assert 1 < attempts < 50


@pytest.mark.asyncio
async def test_create_flushes_messages_before_adding_run(monkeypatch) -> None:
    """run.assistant_message_id 外键指向同批插入的 assistant_message。
    模型未声明 relationship()，SQLAlchemy UOW 无法推断顺序，必须先 flush 两条
    message 再 add run，否则触发 ForeignKeyViolation（曾误报为"当前会话仍在生成"）。
    """
    from unittest.mock import AsyncMock, MagicMock

    from noesis.storage.postgres.models.chat import TAgentRun, TChatMessage

    sequence: list[str] = []

    db = MagicMock()
    db.add = MagicMock(side_effect=lambda obj: sequence.append(
        f"add:{'run' if isinstance(obj, TAgentRun) else 'message'}"
    ))
    db.flush = AsyncMock(side_effect=lambda: sequence.append("flush"))
    db.commit = AsyncMock(side_effect=lambda: sequence.append("commit"))

    session_row = MagicMock()
    session_row.title = "新对话"
    session_row.next_message_sequence = 7
    session_result = MagicMock()
    session_result.scalar_one_or_none.return_value = session_row
    db.execute = AsyncMock(return_value=session_result)

    repo = MagicMock()
    repo.get_by_client_request = AsyncMock(return_value=None)
    repo.get_active_for_session = AsyncMock(return_value=None)
    monkeypatch.setattr(run_service, "AgentRunRepository", lambda _db: repo)
    monkeypatch.setattr(RunService, "_ensure_started_or_finalize", AsyncMock())

    request = CreateRunRequest(
        session_id="session-1",
        content="hello",
        client_request_id="client-req-12345678",
        extra={"qa_type": "COMMON_QA"},
    )
    current_user = MagicMock()
    current_user.user_id = 1

    created = await RunService.create(request, current_user, db)

    # 必须先 add 两条 message → flush → add run → flush → commit
    assert sequence == [
        "add:message",
        "add:message",
        "flush",
        "add:run",
        "flush",
        "commit",
    ], sequence
    added_messages = [call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], TChatMessage)]
    assert [message.message_sequence for message in added_messages] == [7, 8]
    assert added_messages[0].role == "user"
    assert added_messages[1].role == "assistant"
    assert session_row.next_message_sequence == 9
    assert session_row.title == "hello"
    assert created.session_id == "session-1"


@pytest.mark.asyncio
async def test_create_rolls_back_integrity_error_from_run_flush(monkeypatch) -> None:
    """run INSERT 在第二次 flush 失败时也必须回滚并按真实约束错误处理。"""
    from unittest.mock import AsyncMock, MagicMock

    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock(
        side_effect=[None, IntegrityError("insert run", {}, RuntimeError("constraint"))]
    )
    db.rollback = AsyncMock()
    db.commit = AsyncMock()
    session_result = MagicMock()
    session_result.scalar_one_or_none.return_value = MagicMock()
    db.execute = AsyncMock(return_value=session_result)

    repo = MagicMock()
    repo.get_by_client_request = AsyncMock(side_effect=[None, None])
    repo.get_active_for_session = AsyncMock(side_effect=[None, None])
    monkeypatch.setattr(run_service, "AgentRunRepository", lambda _db: repo)

    request = CreateRunRequest(
        session_id="session-1",
        content="hello",
        client_request_id="client-req-integrity",
        extra={"qa_type": "COMMON_QA"},
    )
    current_user = MagicMock(user_id=1)

    with pytest.raises(ServiceException) as exc_info:
        await RunService.create(request, current_user, db)

    assert exc_info.value.message == "创建任务失败，请稍后重试"
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_committed_run_start_failure_is_finalized(monkeypatch) -> None:
    """commit 后 producer 注册失败时不得留下 queued run。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    run = SimpleNamespace(id="run-start-fail", session_id="session-1")
    request = MagicMock()
    current_user = MagicMock()
    monkeypatch.setattr(
        RunService,
        "_ensure_started",
        AsyncMock(side_effect=RuntimeError("cannot register producer")),
    )
    cleanup = AsyncMock()
    monkeypatch.setattr(RunService, "_finalize_start_failure", cleanup)

    with pytest.raises(ServiceException) as exc_info:
        await RunService._ensure_started_or_finalize(run, request, current_user)

    assert exc_info.value.message == "任务启动失败，请稍后重试"
    cleanup.assert_awaited_once_with(run)


@pytest.mark.asyncio
async def test_start_failure_cleanup_finalizes_queued_run(monkeypatch) -> None:
    """启动失败清理必须同时收口 run 与 assistant，而不是只移除内存 handle。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    run = SimpleNamespace(id="run-start-fail", session_id="session-1")
    db = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    class DbContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    repository = MagicMock()
    repository.finalize = AsyncMock(return_value=True)
    monkeypatch.setattr(run_service.run_manager, "get", MagicMock(side_effect=KeyError))
    monkeypatch.setattr("noesis.storage.postgres.manager.pg_manager.get_async_session_context", DbContext)
    monkeypatch.setattr(run_service, "AgentRunRepository", lambda _db: repository)

    await RunService._finalize_start_failure(run)

    kwargs = repository.finalize.await_args.kwargs
    assert kwargs["target"] == RunStatus.ERROR
    assert kwargs["assistant_status"] == "error"
    assert kwargs["error_code"] == "RUN_START_FAILED"
    db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# §5.1–§5.3  active-run API、409 schema
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_active_run_returns_none_when_no_active_run() -> None:
    """无 active Run 时 get_active_run 返回 None。"""
    from unittest.mock import AsyncMock, MagicMock

    repository = MagicMock()
    repository.get_active_for_session = AsyncMock(return_value=None)
    db = MagicMock()

    with patch.object(run_service, "AgentRunRepository", return_value=repository), \
         patch.object(run_service.ChatService, "get_session_by_id", AsyncMock(return_value=object())):
        result = await RunService.get_active_run("session-1", "user-1", db)

    assert result is None


@pytest.mark.asyncio
async def test_get_active_run_returns_live_snapshot() -> None:
    """有 active Run 且 owner 可达时返回 live snapshot。"""
    from unittest.mock import AsyncMock, MagicMock
    from types import SimpleNamespace

    projection = RunProjection(
        run_id="run-active",
        user_id="user-1",
        session_id="session-1",
        assistant_message_id="msg-active",
        qa_type="COMMON_QA",
    )
    row = SimpleNamespace(
        id="run-active",
        user_id="user-1",
        session_id="session-1",
        assistant_message_id="msg-active",
        qa_type="COMMON_QA",
        origin="web",
        status="running",
        last_sequence=0,
        attempt_id=1,
        snapshot={"parts": []},
        finish_reason=None,
        error_code=None,
        user_error_message=None,
        retry_attempt=0,
        retry_max=0,
        updated_at=0,
    )
    repository = MagicMock()
    repository.get_active_for_session = AsyncMock(return_value=row)

    manager = RunManager()
    handle = await manager.start(
        run_id="run-active",
        session_id="session-1",
        user_id="user-1",
        assistant_message_id="msg-active",
        snapshot_provider=projection.snapshot,
        producer=_noop,
        state=projection,
    )
    await handle.producer_task

    db = MagicMock()
    with patch.object(run_service, "AgentRunRepository", return_value=repository), \
         patch.object(run_service.ChatService, "get_session_by_id", AsyncMock(return_value=object())), \
         patch.object(run_service, "run_manager", manager):
        result = await RunService.get_active_run("session-1", "user-1", db)

    assert result is not None
    assert result.run_id == "run-active"
    assert result.session_id == "session-1"
    assert result.assistant_message_id == "msg-active"
    await manager.transition("run-active", RunStatus.COMPLETED)


@pytest.mark.asyncio
async def test_get_active_run_returns_db_snapshot_when_owner_unavailable() -> None:
    """有 active Run 但 owner 不可达时返回 DB snapshot。"""
    from unittest.mock import AsyncMock, MagicMock
    from types import SimpleNamespace

    row = SimpleNamespace(
        id="run-orphan",
        user_id="user-1",
        session_id="session-1",
        assistant_message_id="msg-orphan",
        qa_type="COMMON_QA",
        origin="web",
        status="running",
        last_sequence=5,
        attempt_id=1,
        snapshot={"parts": [{"type": "text", "text": "db content"}]},
        finish_reason=None,
        error_code=None,
        user_error_message=None,
        retry_attempt=0,
        retry_max=0,
        updated_at=1700000000000,
    )
    repository = MagicMock()
    repository.get_active_for_session = AsyncMock(return_value=row)

    db = MagicMock()
    # run_manager.get raises KeyError (owner unavailable)
    with patch.object(run_service, "AgentRunRepository", return_value=repository), \
         patch.object(run_service.ChatService, "get_session_by_id", AsyncMock(return_value=object())), \
         patch.object(run_service, "run_manager", MagicMock(get=MagicMock(side_effect=KeyError))):
        result = await RunService.get_active_run("session-1", "user-1", db)

    assert result is not None
    assert result.run_id == "run-orphan"
    assert result.sequence == 5
    assert len(result.parts) == 1


@pytest.mark.asyncio
async def test_conflict_409_response_contains_full_join_schema() -> None:
    """409 创建冲突响应包含 run_id/assistant_message_id/session_id/status。"""
    from unittest.mock import AsyncMock, MagicMock
    from types import SimpleNamespace

    active_row = SimpleNamespace(
        id="run-existing",
        assistant_message_id="msg-existing",
        session_id="session-1",
        status="running",
    )
    repository = MagicMock()
    repository.get_by_client_request = AsyncMock(return_value=None)
    repository.get_active_for_session = AsyncMock(return_value=active_row)

    db = MagicMock()
    request = CreateRunRequest(
        content="hello",
        session_id="session-1",
        client_request_id="req-0001",
    )
    current_user = SimpleNamespace(user_id="user-1")

    with patch.object(run_service, "AgentRunRepository", return_value=repository):
        with pytest.raises(ConflictException) as exc_info:
            await RunService.create(request, current_user, db)

    conflict_data = exc_info.value.data
    assert conflict_data["run_id"] == "run-existing"
    assert conflict_data["assistant_message_id"] == "msg-existing"
    assert conflict_data["session_id"] == "session-1"
    assert conflict_data["status"] == "running"
