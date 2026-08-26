"""SSE 端点与流式路由的 HTTP 契约。

run 流的响应头、终态 [DONE]、订阅超限 429、owner 不可达 503；会话信令端点
的错误路径。信令/正常流的 happy path 依赖真实事件时序，归 tests/api/。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from noesis.chat.runs import RunStatus, RunSnapshot
from noesis.errors.exceptions import NotFoundException
from noesis.services.chat_service import ChatService
from noesis.services.run_service import RunService


def _snapshot(status: RunStatus) -> RunSnapshot:
    return RunSnapshot(
        run_id="run-1",
        user_id="1",
        session_id="sess-1",
        assistant_message_id="msg-1",
        qa_type="COMMON_QA",
        origin="web",
        status=status,
        sequence=3,
        attempt_id=1,
    )


def test_stream_unknown_run_returns_404_envelope(contract_client) -> None:
    with patch.object(
        RunService,
        "get",
        AsyncMock(side_effect=NotFoundException(message="任务不存在")),
    ):
        resp = contract_client.get("/api/chat/runs/run-missing/stream")
    assert resp.status_code == 404
    assert resp.json()["code"] == 404


def test_stream_subscription_limit_returns_429(contract_client) -> None:
    from noesis.chat.runs import SubscriptionLimitExceeded

    with (
        patch.object(RunService, "get", AsyncMock(return_value=_snapshot(RunStatus.COMPLETED))),
        patch.object(
            RunService,
            "subscribe",
            AsyncMock(side_effect=SubscriptionLimitExceeded()),
        ),
    ):
        resp = contract_client.get("/api/chat/runs/run-1/stream")
    assert resp.status_code == 429
    body = resp.json()
    assert body["code"] == 429
    assert body["data"]["error_code"] == "SSE_SUBSCRIPTION_LIMIT"


def test_terminal_run_stream_returns_snapshot_then_done(contract_client) -> None:
    """终态 run + owner 不可达：SSE 头契约 + snapshot 首帧 + [DONE] 收尾。"""
    with (
        patch.object(RunService, "get", AsyncMock(return_value=_snapshot(RunStatus.COMPLETED))),
        patch.object(RunService, "subscribe", AsyncMock(return_value=None)),
    ):
        with contract_client.stream("GET", "/api/chat/runs/run-1/stream") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            assert resp.headers["cache-control"] == "no-cache"
            assert resp.headers["x-accel-buffering"] == "no"
            body = "".join(resp.iter_text())
    assert "event: run-snapshot" in body
    assert '"status": "completed"' in body
    assert "data: [DONE]" in body


def test_nonterminal_run_owner_unavailable_returns_503(contract_client) -> None:
    """非终态 run + owner 不可达：不创建第二 producer，503 + RUN_OWNER_UNAVAILABLE。"""
    with (
        patch.object(RunService, "get", AsyncMock(return_value=_snapshot(RunStatus.RUNNING))),
        patch.object(RunService, "subscribe", AsyncMock(return_value=None)),
    ):
        resp = contract_client.get("/api/chat/runs/run-1/stream")
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == 503
    assert body["data"]["error_code"] == "RUN_OWNER_UNAVAILABLE"
    assert body["data"]["status"] == "running"


def test_session_events_unknown_session_returns_404(contract_client) -> None:
    with patch.object(ChatService, "get_session_by_id", AsyncMock(return_value=None)):
        resp = contract_client.get("/api/chat/sessions/sess-missing/events")
    assert resp.status_code == 404
    assert resp.json()["code"] == 404


def test_session_events_signal_limit_returns_429(contract_client) -> None:
    from noesis.chat.runs import session_signal_bus

    session = MagicMock()
    with (
        patch.object(ChatService, "get_session_by_id", AsyncMock(return_value=session)),
        patch.object(session_signal_bus, "subscribe", return_value=None),
    ):
        resp = contract_client.get("/api/chat/sessions/sess-1/events")
    assert resp.status_code == 429
    body = resp.json()
    assert body["code"] == 429
    assert body["data"]["error_code"] == "SESSION_SIGNAL_LIMIT"
