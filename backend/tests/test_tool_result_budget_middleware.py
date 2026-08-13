"""Unit contracts for ``ToolResultBudgetMiddleware`` (deterministic replacement)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from noesis.middleware.tool_result_budget_middleware import (
    ReplacementRecord,
    ToolResultBudgetMiddleware,
)


@dataclass
class _FakeWriteResult:
    error: str | None = None


class _FakeBackend:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.written: dict[str, str] = {}

    def write(self, path: str, content: str) -> _FakeWriteResult:
        if self.fail:
            return _FakeWriteResult(error="backend_down")
        self.written[path] = content
        return _FakeWriteResult(error=None)


def _call_request(call_id: str = "c1", state=None) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": "search", "args": {}, "id": call_id},
        tool=None,
        state=state if state is not None else {"messages": []},
        runtime=None,
    )


def _big_result(content: str, call_id: str = "c1", **extra) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=call_id, name="search", **extra)


def _handler_returning(msg):
    def handler(_request):  # noqa: ANN001
        return msg
    return handler


def test_small_result_passes_through_unchanged() -> None:
    mw = ToolResultBudgetMiddleware(_FakeBackend(), max_chars=100)
    small = _big_result("short")
    out = mw.wrap_tool_call(_call_request(), _handler_returning(small))
    assert out is small


def test_large_result_replaced_with_artifact_reference() -> None:
    backend = _FakeBackend()
    mw = ToolResultBudgetMiddleware(backend, max_chars=100)
    big = _big_result("x" * 500)
    out = mw.wrap_tool_call(_call_request("c1"), _handler_returning(big))
    assert isinstance(out, Command)  # carries state update + replacement message
    replaced = out.update["messages"][0]
    assert isinstance(replaced, ToolMessage)
    assert replaced.tool_call_id == "c1"
    assert "artifact_path: /large_tool_results/c1" in replaced.content
    assert "synopsis:" in replaced.content
    # backend was written
    assert "/large_tool_results/c1" in backend.written
    assert backend.written["/large_tool_results/c1"] == "x" * 500


def test_replacement_preserves_status_category_and_outcome() -> None:
    mw = ToolResultBudgetMiddleware(_FakeBackend(), max_chars=10)
    big = _big_result(
        "x" * 200,
        status="error",
        additional_kwargs={"errorCategory": "infrastructure", "outcome": "command_failed"},
    )
    out = mw.wrap_tool_call(_call_request(), _handler_returning(big))
    replaced = out.update["messages"][0]
    assert replaced.status == "error"
    assert replaced.additional_kwargs["errorCategory"] == "infrastructure"
    assert replaced.additional_kwargs["outcome"] == "command_failed"
    assert replaced.tool_call_id == big.tool_call_id


def test_already_artifact_referenced_result_not_re_offloaded() -> None:
    backend = _FakeBackend()
    mw = ToolResultBudgetMiddleware(backend, max_chars=10)
    already = _big_result(
        "x" * 500,
        additional_kwargs={"artifact_path": "/artifacts/pre-existing"},
    )
    out = mw.wrap_tool_call(_call_request(), _handler_returning(already))
    # not replaced, not offloaded again
    assert out is already
    assert backend.written == {}


def test_backend_failure_falls_back_to_text_without_changing_status() -> None:
    mw = ToolResultBudgetMiddleware(_FakeBackend(fail=True), max_chars=10)
    big = _big_result("x" * 2000, status="error")
    out = mw.wrap_tool_call(_call_request(), _handler_returning(big))
    replaced = out.update["messages"][0]
    assert isinstance(replaced, ToolMessage)
    assert replaced.status == "error"
    # fallback: no artifact path, but a bounded synopsis is still present
    assert "text fallback" in replaced.content
    assert "chars omitted" in replaced.content


def test_resume_replays_same_decision_from_record() -> None:
    backend = _FakeBackend()
    mw = ToolResultBudgetMiddleware(backend, max_chars=100)
    big = _big_result("x" * 500, call_id="cc")

    state = {"messages": []}
    out = mw.wrap_tool_call(_call_request("cc", state=state), _handler_returning(big))
    records: dict = out.update["_tool_result_replacements"]
    assert "cc" in records
    record = ReplacementRecord(**records["cc"]) if isinstance(records["cc"], dict) else records["cc"]
    assert record.tool_call_id == "cc"
    assert record.artifact_path == "/large_tool_results/cc"

    # Second invocation with the same state (simulate resume): must replay the
    # recorded decision, not offload again. Replay returns the bounded
    # ToolMessage directly (the record is already in state, no new update).
    state2 = {"messages": [], "_tool_result_replacements": records}
    out2 = mw.wrap_tool_call(_call_request("cc", state=state2), _handler_returning(big))
    assert isinstance(out2, ToolMessage)
    assert "artifact_path: /large_tool_results/cc" in out2.content
    # backend write happened exactly once (during the first call)
    assert list(backend.written.keys()) == ["/large_tool_results/cc"]


def test_non_text_content_not_replaced() -> None:
    mw = ToolResultBudgetMiddleware(_FakeBackend(), max_chars=10)
    big = _big_result([{"type": "image", "source_type": "base64", "data": "x" * 500}])
    out = mw.wrap_tool_call(_call_request(), _handler_returning(big))
    # non-text content that has no text projection is kept as-is
    assert out is big
