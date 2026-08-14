"""Unit contracts for ``SnipMiddleware`` (net-new projection, no upstream)."""

from __future__ import annotations

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from noesis.agents.middlewares.snip_middleware import (
    SnipError,
    SnipMiddleware,
    SnipSelector,
    apply_snip_projection,
)


def _request(messages, state=None):
    return ModelRequest(
        model=object(),  # type: ignore[arg-type]
        messages=list(messages),
        system_message=SystemMessage(content="sys"),
        state=state if state is not None else {"messages": list(messages)},
    )


def _transcript():
    return [
        HumanMessage(content="old question 1"),
        AIMessage(content="old answer 1"),
        HumanMessage(content="old question 2"),
        AIMessage(content="old answer 2"),
        HumanMessage(content="current request"),
    ]


def test_request_snip_produces_record_with_hash_and_tokens() -> None:
    mw = SnipMiddleware()
    msgs = _transcript()
    record = mw.request_snip(msgs, SnipSelector(0, 4), reason="stale context")
    assert record.reason == "stale context"
    assert len(record.original_hash) == 16
    assert record.tokens_freed > 0


def test_modify_request_replaces_snipped_range_with_marker_not_delete() -> None:
    mw = SnipMiddleware()
    msgs = _transcript()
    record = mw.request_snip(msgs, SnipSelector(0, 4), reason="stale")
    state = {"messages": list(msgs), "_snip_records": [record]}
    modified = mw.modify_request(_request(msgs, state=state))

    effective = modified.messages
    # raw transcript untouched
    assert len(msgs) == 5
    # 4 snipped -> 1 marker + 1 remaining user request = 2
    assert len(effective) == 2
    assert isinstance(effective[0], HumanMessage)
    assert "[snipped:" in effective[0].content
    assert effective[1].content == "current request"


def test_no_records_means_identity_projection() -> None:
    mw = SnipMiddleware()
    msgs = _transcript()
    modified = mw.modify_request(_request(msgs, state={"messages": list(msgs)}))
    assert [m.content for m in modified.messages] == [m.content for m in msgs]


def test_cannot_snip_current_user_request() -> None:
    mw = SnipMiddleware()
    msgs = _transcript()
    # range covering the last HumanMessage (index 4)
    with pytest.raises(SnipError, match="current user request"):
        mw.request_snip(msgs, SnipSelector(2, 5), reason="x")


def test_cannot_split_tool_call_result_pair() -> None:
    mw = SnipMiddleware()
    msgs = [
        HumanMessage(content="q"),
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "call-1"}]),
        ToolMessage(content="result", tool_call_id="call-1"),
        HumanMessage(content="current"),
    ]
    # snipping index 1..2 removes the AIMessage(tool_call) but keeps the
    # ToolMessage(result) -> pair split
    with pytest.raises(SnipError, match="tool call/result pair"):
        mw.request_snip(msgs, SnipSelector(1, 2), reason="x")


def test_snipping_a_full_pair_is_allowed() -> None:
    mw = SnipMiddleware()
    msgs = [
        HumanMessage(content="q"),
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "call-1"}]),
        ToolMessage(content="result", tool_call_id="call-1"),
        HumanMessage(content="current"),
    ]
    record = mw.request_snip(msgs, SnipSelector(1, 3), reason="drop pair")
    assert record.selector.start == 1


def test_selector_validation_rejects_inverted_range() -> None:
    with pytest.raises(SnipError):
        SnipSelector(3, 1)
    with pytest.raises(SnipError):
        SnipSelector(-1, 2)


def test_resume_replays_same_projection_deterministically() -> None:
    mw = SnipMiddleware()
    msgs = _transcript()
    record = mw.request_snip(msgs, SnipSelector(0, 2), reason="stale")

    first = apply_snip_projection(msgs, [record])
    second = apply_snip_projection(msgs, [record])
    assert [m.content for m in first] == [m.content for m in second]


def test_wrap_model_call_passes_projected_request_to_handler() -> None:
    mw = SnipMiddleware()
    msgs = _transcript()
    record = mw.request_snip(msgs, SnipSelector(0, 4), reason="stale")
    state = {"messages": list(msgs), "_snip_records": [record]}

    seen = []

    def handler(req):
        seen.append(req.messages)
        return "ok"

    out = mw.wrap_model_call(_request(msgs, state=state), handler)
    assert out == "ok"
    assert len(seen[0]) == 2


def test_selector_replays_after_history_is_prefixed() -> None:
    mw = SnipMiddleware()
    msgs = _transcript()
    record = mw.request_snip(msgs, SnipSelector(0, 2), reason="stale")
    prefixed = [SystemMessage(content="new stable prefix"), *msgs]

    projected = apply_snip_projection(prefixed, [record])

    assert projected[0].content == "new stable prefix"
    assert "[snipped:" in projected[1].content
    assert projected[-1].content == "current request"


def test_cannot_snip_compaction_boundary() -> None:
    mw = SnipMiddleware()
    msgs = [
        HumanMessage(
            content="[conversation summary]",
            additional_kwargs={"lc_source": "summarization", "boundary_hash": "b1"},
        ),
        HumanMessage(content="current request"),
    ]

    with pytest.raises(SnipError, match="compaction boundary"):
        mw.request_snip(msgs, SnipSelector(0, 1), reason="x")


def test_cannot_snip_any_message_in_current_request_turn() -> None:
    mw = SnipMiddleware()
    msgs = [
        HumanMessage(content="old"),
        AIMessage(content="old answer"),
        HumanMessage(content="current request"),
        AIMessage(content="current partial answer"),
    ]

    with pytest.raises(SnipError, match="current user request"):
        mw.request_snip(msgs, SnipSelector(3, 4), reason="x")


def test_unpaired_tool_call_outside_selector_does_not_block_snip() -> None:
    mw = SnipMiddleware()
    msgs = [
        HumanMessage(content="old"),
        AIMessage(content="old answer"),
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "pending"}]),
        HumanMessage(content="current"),
    ]

    record = mw.request_snip(msgs, SnipSelector(0, 2), reason="old turn")
    assert record.selector == SnipSelector(0, 2)


def test_replay_skips_selector_that_would_now_touch_current_turn() -> None:
    mw = SnipMiddleware()
    original = [
        AIMessage(content="target"),
        HumanMessage(content="current request"),
    ]
    record = mw.request_snip(original, SnipSelector(0, 1), reason="old")
    reordered = [
        HumanMessage(content="current request"),
        AIMessage(content="target"),
    ]

    projected = apply_snip_projection(reordered, [record])

    assert [message.content for message in projected] == ["current request", "target"]
