"""Unit contracts for ``CompactionMiddleware`` (self-contained compaction)."""

from __future__ import annotations

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from noesis.middleware.compaction_middleware import (
    CompactionMiddleware,
    CompactionThresholds,
)


def _thresholds(auto_at: int = 50) -> CompactionThresholds:
    # auto_compact_at = model_input_limit - reserve - buffer == auto_at
    return CompactionThresholds(
        model_input_limit=auto_at + 20,
        summary_output_reserve=10,
        transient_request_buffer=10,
    )


def _request(messages, state=None) -> ModelRequest:
    return ModelRequest(
        model=object(),  # type: ignore[arg-type]
        messages=list(messages),
        system_message=SystemMessage(content="sys"),
        state=state if state is not None else {"messages": list(messages)},
    )


def _big_transcript(n: int = 30):
    return [HumanMessage(content=f"msg {i} " + "x" * 20) for i in range(n)]


def test_no_compaction_below_threshold() -> None:
    calls = []

    def handler(req):
        calls.append(len(req.messages))
        return "ok"

    mw = CompactionMiddleware(
        token_counter=lambda msgs: 10,
        summarize=lambda msgs: "summary",
        thresholds=_thresholds(auto_at=50),
    )
    out = mw.wrap_model_call(_request(_big_transcript(5)), handler)
    assert out == "ok"
    assert calls[0] == 5  # unchanged


def test_auto_compaction_replaces_prefix_with_summary() -> None:
    seen = []

    def handler(req):
        seen.append(req.messages)
        return "ok"

    mw = CompactionMiddleware(
        token_counter=lambda msgs: 200,  # over threshold
        summarize=lambda msgs: "Here is the summary.",
        thresholds=_thresholds(auto_at=50),
        keep_messages=4,
    )
    msgs = _big_transcript(20)
    mw.wrap_model_call(_request(msgs), handler)
    compacted = seen[0]
    assert compacted[0].content.startswith("[conversation summary]")
    assert "Here is the summary." in compacted[0].content
    assert len(compacted) == 1 + 4  # summary + preserved tail
    # raw transcript untouched
    assert len(msgs) == 20


def test_empty_summary_does_not_publish_state() -> None:
    seen = []

    def handler(req):
        seen.append(req)
        return "ok"

    mw = CompactionMiddleware(
        token_counter=lambda msgs: 200,
        summarize=lambda msgs: "",  # empty → failure
        thresholds=_thresholds(auto_at=50),
        max_consecutive_failures=5,
    )
    msgs = _big_transcript(20)
    mw.wrap_model_call(_request(msgs), handler)
    # failure → no summarisation event, raw messages passed through
    assert len(seen[0].messages) == 20
    assert "_summarization_event" not in seen[0].state
    assert mw._consecutive_failures == 1


def test_error_marker_summary_treated_as_failure() -> None:
    seen = []

    def handler(req):
        seen.append(req)
        return "ok"

    mw = CompactionMiddleware(
        token_counter=lambda msgs: 200,
        summarize=lambda msgs: "I cannot summarize this conversation.",
        thresholds=_thresholds(auto_at=50),
    )
    mw.wrap_model_call(_request(_big_transcript(20)), handler)
    assert "_summarization_event" not in seen[0].state


def test_ptl_retry_drops_oldest_prefix_then_succeeds() -> None:
    calls = []

    def summarize(msgs):
        calls.append(len(msgs))
        if len(msgs) > 10:
            raise ContextOverflowError("too big")
        return "summary after retry"

    mw = CompactionMiddleware(
        token_counter=lambda msgs: 200,
        summarize=summarize,
        thresholds=_thresholds(auto_at=50),
        keep_messages=4,
        max_ptl_retries=8,
    )
    seen = []
    mw.wrap_model_call(_request(_big_transcript(20)), lambda req: seen.append(req) or "ok")
    # retried by dropping prefix until len <= 10
    assert calls[-1] <= 10
    assert "summary after retry" in seen[0].messages[0].content


def test_ptl_retries_exhausted_returns_failure() -> None:
    def summarize(msgs):
        raise ContextOverflowError("always too big")

    mw = CompactionMiddleware(
        token_counter=lambda msgs: 200,
        summarize=summarize,
        thresholds=_thresholds(auto_at=50),
        keep_messages=4,
        max_ptl_retries=2,
        max_consecutive_failures=5,
    )
    seen = []
    mw.wrap_model_call(_request(_big_transcript(20)), lambda req: seen.append(req) or "ok")
    assert "_summarization_event" not in seen[0].state
    assert mw._consecutive_failures == 1


def test_reactive_overflow_recovery_compacts_and_retries() -> None:
    attempts = []

    def handler(req):
        attempts.append(len(req.messages))
        if len(attempts) == 1:
            raise ContextOverflowError("overflow on full history")
        return "ok"

    mw = CompactionMiddleware(
        token_counter=lambda msgs: 10,  # below auto threshold
        summarize=lambda msgs: "reactive summary",
        thresholds=_thresholds(auto_at=50),
        keep_messages=4,
    )
    out = mw.wrap_model_call(_request(_big_transcript(20)), handler)
    assert out == "ok"
    assert attempts[0] == 20  # first attempt full
    assert attempts[1] == 1 + 4  # reactive compaction → summary + tail


def test_consecutive_failures_open_breaker_stops_auto_compaction() -> None:
    def summarize(msgs):
        return ""  # always fails

    mw = CompactionMiddleware(
        token_counter=lambda msgs: 200,
        summarize=summarize,
        thresholds=_thresholds(auto_at=50),
        max_consecutive_failures=2,
    )
    seen = []
    for _ in range(3):
        mw.wrap_model_call(_request(_big_transcript(20)), lambda req: seen.append(len(req.messages)) or "ok")
    # after 2 failures, breaker opens; 3rd call no longer attempts (passes through)
    assert mw._breaker_open is True


def test_boundary_does_not_split_tool_pair() -> None:
    def handler(req):
        assert all(not isinstance(m, ToolMessage) or any(
            isinstance(m2, AIMessage) for m2 in req.messages
        ) for m in req.messages)
        return "ok"

    # place a ToolMessage just at the boundary so naive cutoff would orphan it
    msgs = _big_transcript(18)
    msgs.append(AIMessage(content="call", tool_calls=[{"name": "t", "args": {}, "id": "c1"}]))
    msgs.append(ToolMessage(content="result", tool_call_id="c1"))
    mw = CompactionMiddleware(
        token_counter=lambda msgs: 200,
        summarize=lambda msgs: "summary",
        thresholds=_thresholds(auto_at=50),
        keep_messages=4,
    )
    mw.wrap_model_call(_request(msgs), handler)
