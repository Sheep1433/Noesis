"""Unit contracts for ``MicroCompactionMiddleware`` (model-free reduction)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from noesis.middleware.micro_compaction_middleware import MicroCompactionMiddleware


@dataclass
class _FakeWriteResult:
    error: str | None = None


class _FakeBackend:
    def __init__(self) -> None:
        self.written: dict[str, str] = {}

    def write(self, path: str, content: str) -> _FakeWriteResult:
        self.written[path] = content
        return _FakeWriteResult(error=None)


def _request(messages, state=None):
    return ModelRequest(
        model=object(),  # type: ignore[arg-type]
        messages=list(messages),
        system_message=SystemMessage(content="sys"),
        state=state if state is not None else {"messages": list(messages)},
    )


def test_recent_window_kept_verbatim() -> None:
    mw = MicroCompactionMiddleware(_FakeBackend(), keep_recent=3, large_tool_result_chars=10)
    recent_tool = ToolMessage(content="small", tool_call_id="r1", name="t")
    msgs = [ToolMessage(content="x" * 500, tool_call_id="old1", name="t"), recent_tool]
    modified = mw.modify_request(_request(msgs))
    # only 2 messages, keep_recent=3 → cutoff=0, nothing reduced
    assert [m.content for m in modified.messages] == [m.content for m in msgs]


def test_old_large_tool_result_replaced_with_artifact_synopsis() -> None:
    backend = _FakeBackend()
    mw = MicroCompactionMiddleware(backend, keep_recent=1, large_tool_result_chars=10)
    big_old = ToolMessage(content="B" * 500, tool_call_id="c1", name="t")
    pair_ai = AIMessage(content="ok", tool_calls=[{"name": "t", "args": {}, "id": "c1"}])
    recent = HumanMessage(content="now")
    msgs = [big_old, pair_ai, recent]
    modified = mw.modify_request(_request(msgs))
    out0 = modified.messages[0]
    assert "micro-compacted" in out0.content
    assert "artifact=/large_tool_results/c1" in out0.content
    assert backend.written["/large_tool_results/c1"] == "B" * 500
    # recent window (last 1) untouched
    assert modified.messages[-1].content == "now"


def test_old_large_tool_arg_truncated_with_head() -> None:
    mw = MicroCompactionMiddleware(_FakeBackend(), keep_recent=1, large_tool_arg_chars=20, arg_head_chars=5)
    big_arg_call = AIMessage(
        content="doing",
        tool_calls=[{"name": "write", "args": {"content": "X" * 200, "path": "/f"}, "id": "w1"}],
    )
    recent = HumanMessage(content="now")
    modified = mw.modify_request(_request([big_arg_call, recent]))
    out_call = modified.messages[0]
    assert out_call.tool_calls[0]["args"]["content"].startswith("XXXXX")
    assert "truncated" in out_call.tool_calls[0]["args"]["content"]
    # path arg under threshold, preserved
    assert out_call.tool_calls[0]["args"]["path"] == "/f"


def test_no_reduction_when_history_short() -> None:
    mw = MicroCompactionMiddleware(_FakeBackend(), keep_recent=10, large_tool_result_chars=10)
    msgs = [ToolMessage(content="x" * 500, tool_call_id="c1", name="t")]
    modified = mw.modify_request(_request(msgs))
    assert modified.messages[0].content == "x" * 500


def test_never_cuts_tool_pair() -> None:
    mw = MicroCompactionMiddleware(_FakeBackend(), keep_recent=1, large_tool_result_chars=10)
    # a ToolMessage whose matching AIMessage is NOT in the projection (already
    # gone) → must not be reduced (would orphan the result)
    orphaned = ToolMessage(content="x" * 500, tool_call_id="ghost", name="t")
    recent = HumanMessage(content="now")
    modified = mw.modify_request(_request([orphaned, recent]))
    # _is_tool_pair_safe returns False for orphaned result → kept as-is
    assert modified.messages[0].content == "x" * 500


def test_idempotent_on_already_reduced_messages() -> None:
    mw = MicroCompactionMiddleware(_FakeBackend(), keep_recent=1, large_tool_result_chars=10)
    big_old = ToolMessage(content="B" * 500, tool_call_id="c1", name="t")
    pair_ai = AIMessage(content="ok", tool_calls=[{"name": "t", "args": {}, "id": "c1"}])
    recent = HumanMessage(content="now")
    msgs = [big_old, pair_ai, recent]
    first = mw.modify_request(_request(msgs))
    # second pass over already-reduced messages: no new records
    second = mw.modify_request(first)
    assert second.messages[0].content == first.messages[0].content


def test_dedupe_hook_drops_only_hooked_indices_in_old_window() -> None:
    mw = MicroCompactionMiddleware(
        _FakeBackend(),
        keep_recent=1,
        dedupe_hook=lambda msgs: [0],  # drop index 0
    )
    dup = ToolMessage(content="dup", tool_call_id="d1", name="t")
    pair = AIMessage(content="ok", tool_calls=[{"name": "t", "args": {}, "id": "d1"}])
    recent = HumanMessage(content="now")
    modified = mw.modify_request(_request([dup, pair, recent]))
    assert "duplicate_drop" in modified.messages[0].content
