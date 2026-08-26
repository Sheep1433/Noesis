"""Unit contracts for ``ToolResultBudgetMiddleware`` (deterministic replacement)."""

from __future__ import annotations

from dataclasses import dataclass

from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from noesis.agents.middlewares.tool_result_budget_middleware import (
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
    assert "artifact_path: /large_tool_results/c1-" in replaced.content
    assert "synopsis:" in replaced.content
    # backend was written
    assert len(backend.written) == 1
    assert next(iter(backend.written.values())) == "x" * 500


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
    assert record.artifact_path is not None
    assert record.artifact_path.startswith("/large_tool_results/cc-")

    # Second invocation with the same state (simulate resume): must replay the
    # recorded decision, not offload again. Replay returns the bounded
    # ToolMessage directly (the record is already in state, no new update).
    state2 = {"messages": [], "_tool_result_replacements": records}
    out2 = mw.wrap_tool_call(_call_request("cc", state=state2), _handler_returning(big))
    assert isinstance(out2, ToolMessage)
    assert "artifact_path: /large_tool_results/cc-" in out2.content
    # backend write happened exactly once (during the first call)
    assert len(backend.written) == 1


def test_non_text_content_not_replaced() -> None:
    mw = ToolResultBudgetMiddleware(_FakeBackend(), max_chars=10)
    big = _big_result([{"type": "image", "source_type": "base64", "data": "x" * 500}])
    out = mw.wrap_tool_call(_call_request(), _handler_returning(big))
    # non-text content that has no text projection is kept as-is
    assert out is big


def test_historical_large_result_is_projected_without_mutating_raw_history() -> None:
    backend = _FakeBackend()
    mw = ToolResultBudgetMiddleware(backend, max_chars=20)
    raw = [
        HumanMessage(content="question"),
        _big_result(
            "large-history-result" * 20,
            call_id="history-call",
            status="error",
            additional_kwargs={"errorCategory": "tool", "outcome": "failed"},
        ),
    ]
    state = {"messages": list(raw)}
    request = ModelRequest(
        model=object(),  # type: ignore[arg-type]
        messages=list(raw),
        system_message=SystemMessage(content="sys"),
        state=state,
    )

    projected = mw.modify_request(request)

    assert state["messages"][1] is raw[1]
    assert state["messages"][1].content.startswith("large-history-result")
    replacement = projected.messages[1]
    assert isinstance(replacement, ToolMessage)
    assert replacement.tool_call_id == "history-call"
    assert replacement.status == "error"
    assert replacement.additional_kwargs["errorCategory"] == "tool"
    assert replacement.additional_kwargs["outcome"] == "failed"
    assert "_tool_result_replacements" not in state
    assert "history-call" in projected.state["_tool_result_replacements"]


def test_historical_replay_uses_record_only_for_matching_content_hash() -> None:
    backend = _FakeBackend()
    mw = ToolResultBudgetMiddleware(backend, max_chars=10)
    first = _big_result("a" * 100, call_id="same-id")
    first_request = ModelRequest(
        model=object(),  # type: ignore[arg-type]
        messages=[first],
        system_message=SystemMessage(content="sys"),
        state={"messages": [first]},
    )
    projected = mw.modify_request(first_request)
    records = projected.state["_tool_result_replacements"]

    changed = _big_result("b" * 100, call_id="same-id")
    resumed_state = {"messages": [changed], "_tool_result_replacements": records}
    resumed = mw.modify_request(
        ModelRequest(
            model=object(),  # type: ignore[arg-type]
            messages=[changed],
            system_message=SystemMessage(content="sys"),
            state=resumed_state,
        ),
    )

    assert "b" in resumed.messages[0].content
    assert len(backend.written) == 2


def test_immediate_command_message_is_replaced_without_losing_command_update() -> None:
    mw = ToolResultBudgetMiddleware(_FakeBackend(), max_chars=10)
    command = Command(
        update={
            "messages": [_big_result("x" * 100, call_id="command-call", status="error")],
            "domain_state": "kept",
        },
    )

    result = mw.wrap_tool_call(_call_request("command-call"), _handler_returning(command))

    assert isinstance(result, Command)
    assert result.update["domain_state"] == "kept"
    replacement = result.update["messages"][0]
    assert isinstance(replacement, ToolMessage)
    assert replacement.tool_call_id == "command-call"
    assert replacement.status == "error"
    assert "command-call" in result.update["_tool_result_replacements"]


def test_parallel_results_are_bounded_by_aggregate_budget() -> None:
    mw = ToolResultBudgetMiddleware(
        _FakeBackend(),
        max_chars=100,
        aggregate_max_chars=120,
    )
    command = Command(
        update={
            "messages": [
                _big_result("a" * 80, call_id="a"),
                _big_result("b" * 80, call_id="b"),
            ],
        },
    )

    result = mw.wrap_tool_call(_call_request("batch"), _handler_returning(command))

    assert isinstance(result, Command)
    assert len(result.update["_tool_result_replacements"]) == 1
    assert sum(
        bool(message.additional_kwargs.get("tool_result_replacement"))
        for message in result.update["messages"]
    ) == 1


def test_old_write_argument_is_replaced_but_recent_argument_is_kept() -> None:
    backend = _FakeBackend()
    mw = ToolResultBudgetMiddleware(
        backend,
        max_chars=20,
        argument_keep_recent_messages=2,
    )
    old = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "write_file",
                "args": {"file_path": "/old.txt", "content": "x" * 100},
                "id": "write-old",
            }
        ],
    )
    recent = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "write_file",
                "args": {"file_path": "/new.txt", "content": "y" * 100},
                "id": "write-new",
            }
        ],
    )
    messages = [old, HumanMessage(content="middle"), recent]
    request = ModelRequest(
        model=object(),  # type: ignore[arg-type]
        messages=messages,
        system_message=SystemMessage(content="sys"),
        state={"messages": messages},
    )

    projected = mw.modify_request(request)

    assert "large content omitted" in projected.messages[0].tool_calls[0]["args"]["content"]
    assert projected.messages[2].tool_calls[0]["args"]["content"] == "y" * 100
    assert "arg:write-old:content" in projected.state["_tool_result_replacements"]


def test_immediate_replacement_is_not_replaced_again_as_history() -> None:
    backend = _FakeBackend()
    mw = ToolResultBudgetMiddleware(backend, max_chars=10)
    immediate = mw.wrap_tool_call(
        _call_request("once"),
        _handler_returning(_big_result("x" * 100, call_id="once")),
    )
    replacement = immediate.update["messages"][0]
    state = {
        "messages": [replacement],
        "_tool_result_replacements": immediate.update["_tool_result_replacements"],
    }
    request = ModelRequest(
        model=object(),  # type: ignore[arg-type]
        messages=[replacement],
        system_message=SystemMessage(content="sys"),
        state=state,
    )

    projected = mw.modify_request(request)

    assert projected.messages[0] is replacement
    assert len(backend.written) == 1
