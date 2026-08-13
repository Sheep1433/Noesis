"""Unit contracts for ``DurableContextMiddleware`` (compaction-safe ledger)."""

from __future__ import annotations

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import SystemMessage

from noesis.middleware.durable_context_middleware import (
    DurableContext,
    DurableContextMiddleware,
)


def _request(state) -> ModelRequest:
    return ModelRequest(model=object(), messages=[], system_message=SystemMessage(content="sys"), state=state)  # type: ignore[arg-type]


def test_empty_state_does_not_inject_block() -> None:
    mw = DurableContextMiddleware()
    modified = mw.modify_request(_request({"messages": []}))
    assert modified.system_message.text == "sys"


def test_set_active_plan_then_injects() -> None:
    mw = DurableContextMiddleware()
    state = {"messages": []}
    mw.set_active_plan(state, "PLAN-1")
    modified = mw.modify_request(_request(state))
    text = modified.system_message.text
    assert "Durable Context" in text
    assert "PLAN-1" in text


def test_add_and_complete_task_updates_ledger() -> None:
    mw = DurableContextMiddleware()
    state = {"messages": []}
    mw.add_task(state, "write tests")
    mw.add_task(state, "write tests")  # dedup
    mw.add_task(state, "review")
    assert DurableContextMiddleware._ctx(state).pending_tasks == ["write tests", "review"]
    mw.complete_task(state, "write tests")
    assert DurableContextMiddleware._ctx(state).pending_tasks == ["review"]


def test_record_delegation_appends_to_ledger() -> None:
    mw = DurableContextMiddleware()
    state = {"messages": []}
    mw.record_delegation(state, "researcher", "/artifacts/r1.md")
    ctx = DurableContextMiddleware._ctx(state)
    assert ctx.delegation_ledger == ["researcher→/artifacts/r1.md"]


def test_merge_refs_dedups() -> None:
    mw = DurableContextMiddleware()
    state = {"messages": []}
    mw.merge_refs(state, skills=["a", "b"], files=["/f1"], tools=["t1"])
    mw.merge_refs(state, skills=["b", "c"], files=["/f2"], tools=["t1"])
    ctx = DurableContextMiddleware._ctx(state)
    assert ctx.loaded_skill_refs == ["a", "b", "c"]
    assert ctx.active_file_refs == ["/f1", "/f2"]
    assert ctx.discovered_tool_refs == ["t1"]


def test_compact_instructions_appear_when_set() -> None:
    mw = DurableContextMiddleware()
    state = {"messages": []}
    mw.set_compact_instructions(state, "keep the migration table")
    text = mw.modify_request(_request(state)).system_message.text
    assert "compact_instructions: keep the migration table" in text


def test_snapshot_whitelist_filters_for_fork() -> None:
    mw = DurableContextMiddleware()
    state = {"messages": []}
    mw.set_active_plan(state, "PLAN")
    mw.add_task(state, "t1")
    mw.record_delegation(state, "sub", "/r")
    snap = mw.snapshot(state, whitelist=("active_plan_ref", "pending_tasks"))
    assert snap.active_plan_ref == "PLAN"
    assert snap.pending_tasks == ["t1"]
    # delegation ledger is NOT whitelisted → fork copy excludes it
    assert snap.delegation_ledger == []


def test_snapshot_full_copy_is_independent() -> None:
    mw = DurableContextMiddleware()
    state = {"messages": []}
    mw.add_task(state, "t1")
    snap = mw.snapshot(state)
    snap.pending_tasks.append("mutated")
    # original state untouched
    assert DurableContextMiddleware._ctx(state).pending_tasks == ["t1"]


def test_block_re_runs_naturally_after_compaction() -> None:
    # Compaction drops conversation messages but private state survives; the
    # durable block is rebuilt from state at the next model call.
    mw = DurableContextMiddleware()
    state = {"messages": ["will be summarised away"]}
    mw.set_active_plan(state, "PLAN-X")
    # simulate post-compaction: messages replaced, state retained
    state["messages"] = ["[summary]"]
    text = mw.modify_request(_request(state)).system_message.text
    assert "PLAN-X" in text
