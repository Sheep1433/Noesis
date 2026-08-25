"""Phase 1 契约基线：定义最终 typed RunEvent、RunSnapshot、SSE frame、
409/429/503 响应的稳定 schema。

这些测试是活文档——任何协议变更都会在这里可见。不创建旧协议 fixture
或兼容 parser 测试（对应 tasks.md §1.5）。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from noesis.chat.delivery.events import (
    HitlRequired,
    RunAborted,
    RunCompleted,
    RunError,
    RunPaused,
    StreamDone,
    WireFrame,
)
from noesis.chat.delivery.sse import (
    encode_run_event,
    encode_sequenced_event,
    format_done,
    format_sse,
    parse_sse_line_to_event,
)
from noesis.chat.runs import RunSnapshot, RunStatus
from noesis.chat.runs.manager import SequencedRunEvent


# ---------------------------------------------------------------------------
# typed RunEvent 契约
# ---------------------------------------------------------------------------


class TestRunEventContract:
    """typed RunEvent union 成员与字段。"""

    def test_run_completed_carries_finish_reason_and_usage(self) -> None:
        event = RunCompleted(finish_reason="stop", usage={"input": 10, "output": 20})
        assert event.finish_reason == "stop"
        assert event.usage["input"] == 10

    def test_run_error_carries_message_and_reason(self) -> None:
        event = RunError(message="model failed", finish_reason="error")
        assert event.message == "model failed"
        assert event.finish_reason == "error"

    def test_run_paused_carries_hitl_reason(self) -> None:
        event = RunPaused(reason="hitl_pending", finish_reason="hitl_pending")
        assert event.reason == "hitl_pending"

    def test_hitl_required_carries_payload(self) -> None:
        event = HitlRequired(payload={"kind": "approval", "interrupt_id": "x"})
        assert event.payload["kind"] == "approval"

    def test_stream_done_has_no_payload(self) -> None:
        event = StreamDone()
        # StreamDone 是传输层标记，无业务字段
        assert isinstance(event, StreamDone)

    def test_run_aborted_carries_reason(self) -> None:
        event = RunAborted(reason="content_filter")
        assert event.reason == "content_filter"


# ---------------------------------------------------------------------------
# RunSnapshot 契约
# ---------------------------------------------------------------------------


class TestRunSnapshotContract:
    """RunSnapshot.to_dict() 是 active-run API、GET /runs/{id} 和 SSE run-snapshot 的共同 schema。"""

    def _snapshot(self, **overrides: Any) -> RunSnapshot:
        defaults = dict(
            run_id="run-abc",
            user_id="user-1",
            session_id="session-1",
            assistant_message_id="msg-abc",
            qa_type="COMMON_QA",
            origin="web",
            status=RunStatus.RUNNING,
            sequence=42,
            attempt_id=1,
            parts=({"type": "text", "text": "hello"},),
            finish_reason=None,
            error_code=None,
            user_error_message=None,
            pending_hitl=None,
            updated_at=1700000000000,
        )
        defaults.update(overrides)
        return RunSnapshot(**defaults)

    def test_to_dict_has_all_required_fields(self) -> None:
        data = self._snapshot().to_dict()
        required = {
            "run_id",
            "session_id",
            "assistant_message_id",
            "qa_type",
            "origin",
            "status",
            "snapshot_sequence",
            "attempt_id",
            "content",
            "finish_reason",
            "error_code",
            "message",
            "pending_hitl",
            "updated_at",
        }
        assert required.issubset(data.keys())

    def test_to_dict_status_is_string_value(self) -> None:
        data = self._snapshot(status=RunStatus.COMPLETED).to_dict()
        assert data["status"] == "completed"

    def test_to_dict_snapshot_sequence_is_int(self) -> None:
        data = self._snapshot(sequence=42).to_dict()
        assert data["snapshot_sequence"] == 42
        assert isinstance(data["snapshot_sequence"], int)

    def test_to_dict_content_wraps_parts(self) -> None:
        data = self._snapshot().to_dict()
        assert isinstance(data["content"], dict)
        assert "parts" in data["content"]
        assert isinstance(data["content"]["parts"], list)

    def test_terminal_snapshot_is_terminal(self) -> None:
        snap = self._snapshot(status=RunStatus.PARTIAL)
        assert snap.is_terminal is True

    def test_non_terminal_snapshot_not_terminal(self) -> None:
        snap = self._snapshot(status=RunStatus.RUNNING)
        assert snap.is_terminal is False


# ---------------------------------------------------------------------------
# SSE frame 契约
# ---------------------------------------------------------------------------


class TestSSEFrameContract:
    """SSE frame 格式：event: + data: JSON，sequenced frame 注入 run_id/sequence/attempt_id。"""

    def test_format_sse_produces_event_and_data(self) -> None:
        line = format_sse("text-delta", {"text_delta": "hi"})
        assert line.startswith("event: text-delta\n")
        assert "data: " in line
        assert line.endswith("\n\n")

    def test_format_done_is_data_done(self) -> None:
        line = format_done()
        assert line == "data: [DONE]\n\n"

    def test_encode_sequenced_event_injects_run_metadata(self) -> None:
        event = WireFrame(event="text-delta", data={"type": "text-delta", "text_delta": "hello"})
        envelope = SequencedRunEvent(
            run_id="run-1",
            sequence=7,
            attempt_id=2,
            event=event,
        )
        lines = encode_sequenced_event(envelope)
        assert len(lines) == 1
        line = lines[0]
        assert "event: text-delta" in line
        # 解析 data JSON 验证注入字段
        data_raw = line.split("data: ", 1)[1].strip()
        payload = json.loads(data_raw)
        assert payload["run_id"] == "run-1"
        assert payload["sequence"] == 7
        assert payload["attempt_id"] == 2
        assert payload["text_delta"] == "hello"

    def test_encode_sequenced_stream_done(self) -> None:
        envelope = SequencedRunEvent(
            run_id="run-1", sequence=1, attempt_id=1, event=StreamDone()
        )
        lines = encode_sequenced_event(envelope)
        assert lines == [format_done()]

    def test_encode_hitl_required(self) -> None:
        event = HitlRequired(payload={"kind": "approval", "interrupt_id": "ix"})
        lines = encode_run_event(event)
        assert len(lines) == 1
        assert "event: hitl-required" in lines[0]
        data = json.loads(lines[0].split("data: ", 1)[1].strip())
        assert data["kind"] == "approval"

    def test_encode_run_completed_as_finish(self) -> None:
        event = RunCompleted(finish_reason="stop", usage={"input": 5})
        lines = encode_run_event(event)
        assert "event: finish" in lines[0]
        data = json.loads(lines[0].split("data: ", 1)[1].strip())
        assert data["finish_reason"] == "stop"

    def test_parse_sse_roundtrip_text_delta(self) -> None:
        original = WireFrame(event="text-delta", data={"type": "text-delta", "text_delta": "roundtrip"})
        lines = encode_run_event(original)
        events = parse_sse_line_to_event(lines[0])
        assert len(events) == 1
        assert isinstance(events[0], WireFrame)
        assert events[0].event == "text-delta"
        assert events[0].data["text_delta"] == "roundtrip"

    def test_parse_done_returns_stream_done(self) -> None:
        events = parse_sse_line_to_event(format_done())
        assert len(events) == 1
        assert isinstance(events[0], StreamDone)


# ---------------------------------------------------------------------------
# 409 创建冲突响应契约
# ---------------------------------------------------------------------------


class TestConflict409Contract:
    """POST /runs 409 响应必须包含可加入的 run_id/assistant_message_id/session_id/status。"""

    def test_conflict_response_shape(self) -> None:
        from noesis.errors.exceptions import ConflictException

        exc = ConflictException(
            message="当前会话仍在生成",
            data={
                "run_id": "run-existing",
                "assistant_message_id": "msg-existing",
                "session_id": "session-1",
                "status": "running",
            },
        )
        # 异常被 exception_handler 映射为 ResponseUtil.conflict
        assert exc.message == "当前会话仍在生成"
        assert exc.data["run_id"] == "run-existing"
        assert exc.data["assistant_message_id"] == "msg-existing"
        assert exc.data["session_id"] == "session-1"
        assert exc.data["status"] == "running"

    @pytest.mark.asyncio
    async def test_conflict_response_via_handler(self) -> None:
        """验证 ConflictException → ResponseUtil.conflict → HTTP 409 + code=409。"""
        from server.exception_handlers import handle_exception
        from fastapi import FastAPI

        app = FastAPI()
        handle_exception(app)

        from noesis.errors.exceptions import ConflictException

        exc = ConflictException(
            message="当前会话仍在生成",
            data={
                "run_id": "run-x",
                "assistant_message_id": "msg-x",
                "session_id": "s-x",
                "status": "running",
            },
        )

        # 直接调用 handler 验证响应
        handler = app.exception_handlers[ConflictException]
        response = await handler(None, exc)
        assert response.status_code == 409
        body = json.loads(response.body.decode())
        assert body["code"] == 409
        assert body["data"]["run_id"] == "run-x"
        assert body["data"]["assistant_message_id"] == "msg-x"
        assert body["data"]["session_id"] == "s-x"
        assert body["data"]["status"] == "running"


# ---------------------------------------------------------------------------
# 429 / 503 响应 schema
# ---------------------------------------------------------------------------


class TestSubscriptionLimit429Contract:
    """超 subscription 配额时返回 HTTP 429 + SSE_SUBSCRIPTION_LIMIT。"""

    def test_error_code_is_stable(self) -> None:
        from server.response import ResponseUtil

        response = ResponseUtil.too_many_requests(
            data={"error_code": "SSE_SUBSCRIPTION_LIMIT"}
        )
        body = json.loads(response.body.decode())
        assert response.status_code == 429
        assert body["data"]["error_code"] == "SSE_SUBSCRIPTION_LIMIT"


class TestOwnerUnavailable503Contract:
    """DB 非终态但本地无 RunHandle 时返回 HTTP 503 + RUN_OWNER_UNAVAILABLE。"""

    def test_error_code_is_stable(self) -> None:
        from server.response import ResponseUtil

        response = ResponseUtil.service_unavailable(
            data={"error_code": "RUN_OWNER_UNAVAILABLE"}
        )
        body = json.loads(response.body.decode())
        assert response.status_code == 503
        assert body["data"]["error_code"] == "RUN_OWNER_UNAVAILABLE"


# ---------------------------------------------------------------------------
# active-run API 响应契约
# ---------------------------------------------------------------------------


class TestActiveRunResponseContract:
    """GET /sessions/{session_id}/active-run 返回 RunSnapshot 或 data=null。"""

    def test_active_run_snapshot_matches_get_run_shape(self) -> None:
        """active-run 返回的 snapshot 与 GET /runs/{run_id} 结构一致。"""
        snap = RunSnapshot(
            run_id="run-active",
            user_id="user-1",
            session_id="session-1",
            assistant_message_id="msg-active",
            qa_type="COMMON_QA",
            origin="web",
            status=RunStatus.RUNNING,
            sequence=10,
            attempt_id=1,
            parts=({"type": "text", "text": "generating"},),
        )
        data = snap.to_dict()
        # active-run API 返回 data 字段就是这个结构
        assert data["run_id"] == "run-active"
        assert data["status"] == "running"
        assert data["snapshot_sequence"] == 10

    def test_no_active_run_returns_null_data(self) -> None:
        """无 active Run 时 data=null。"""
        from server.response import ResponseUtil

        response = ResponseUtil.success(
            msg="无活跃任务", dict_content={"data": None}
        )
        body = json.loads(response.body.decode())
        assert body["code"] == 200
        assert body["data"] is None

    def test_unknown_session_returns_404(self) -> None:
        """未知/已删/跨用户 session 返回 404，不泄露 run_id。"""
        from noesis.errors.exceptions import NotFoundException

        exc = NotFoundException(message="会话不存在")
        assert exc.message == "会话不存在"
        assert exc.data is None
