"""Unit contracts for ``SubAgentContextMiddleware`` (isolated/fork/resume)."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from noesis.middleware.subagents_middleware import (
    PARENT_ONLY_KEYS,
    SubAgentContextMiddleware,
    SubAgentContextMode,
    SubAgentContextPolicy,
    prepare_subagent_state,
)


def _parent_state():
    return {
        "messages": [HumanMessage(content="parent q"), AIMessage(content="parent a")],
        "todos": ["t1"],
        "skills_metadata": ["skill1"],
        "memory_contents": {"m": "1"},
        "_durable_context": {
            "active_plan_ref": "PLAN",
            "pending_tasks": ["pt1"],
            "delegation_ledger": ["sub→/r"],
        },
        "_file_context": {"/a": {"mtime": "1"}},
        "_source_revision": {"revision": "abc"},
        "_snip_records": [],
        "allowed_skill_ref": "shared-skill",
    }


def test_isolated_child_receives_only_description_and_whitelisted_stable() -> None:
    parent = _parent_state()
    policy = SubAgentContextPolicy(
        mode=SubAgentContextMode.ISOLATED,
        stable_context_whitelist=("allowed_skill_ref",),
    )
    child = prepare_subagent_state(parent, "do research", policy)
    assert [m.content for m in child["messages"]] == ["do research"]
    assert child["allowed_skill_ref"] == "shared-skill"
    # parent-only and private state never leak
    for key in ("todos", "skills_metadata", "memory_contents", "_durable_context", "_file_context", "_source_revision", "_snip_records"):
        assert key not in child


def test_isolated_does_not_mutate_parent_state() -> None:
    parent = _parent_state()
    policy = SubAgentContextPolicy(mode=SubAgentContextMode.ISOLATED, stable_context_whitelist=("allowed_skill_ref",))
    child = prepare_subagent_state(parent, "task", policy)
    child["allowed_skill_ref"] = "mutated"
    # parent copy untouched
    assert parent["allowed_skill_ref"] == "shared-skill"


def test_fork_copies_conversation_snapshot_and_whitelisted_durable() -> None:
    parent = _parent_state()
    policy = SubAgentContextPolicy(
        mode=SubAgentContextMode.FORK,
        durable_whitelist=("active_plan_ref", "pending_tasks"),
    )
    child = prepare_subagent_state(parent, "continue work", policy)
    # snapshot = description + parent messages
    assert child["messages"][0].content == "continue work"
    assert child["messages"][1].content == "parent q"
    assert child["messages"][2].content == "parent a"
    # durable whitelist copied
    assert child["_durable_context"]["active_plan_ref"] == "PLAN"
    assert child["_durable_context"]["pending_tasks"] == ["pt1"]
    # non-whitelisted durable field excluded
    assert "delegation_ledger" not in child["_durable_context"]
    # other private state still excluded
    assert "_file_context" not in child
    assert "_source_revision" not in child


def test_fork_deep_copies_so_child_mutation_does_not_affect_parent() -> None:
    parent = _parent_state()
    policy = SubAgentContextPolicy(
        mode=SubAgentContextMode.FORK,
        durable_whitelist=("pending_tasks",),
    )
    child = prepare_subagent_state(parent, "task", policy)
    child["messages"].append(HumanMessage(content="child extra"))
    child["_durable_context"]["pending_tasks"].append("child task")
    # parent untouched
    assert len(parent["messages"]) == 2
    assert parent["_durable_context"]["pending_tasks"] == ["pt1"]


def test_resume_does_not_read_parent_current_state() -> None:
    parent = _parent_state()
    policy = SubAgentContextPolicy(
        mode=SubAgentContextMode.RESUME,
        subagent_thread_id="child-thread-1",
    )
    child = prepare_subagent_state(parent, None, policy)
    # resume seeds nothing from parent — history comes from child's own checkpoint
    assert child == {}
    for key in PARENT_ONLY_KEYS:
        assert key not in child


def test_resume_requires_thread_id() -> None:
    parent = _parent_state()
    policy = SubAgentContextPolicy(mode=SubAgentContextMode.RESUME)
    with pytest.raises(ValueError, match="subagent_thread_id"):
        prepare_subagent_state(parent, "x", policy)


def test_middleware_registry_resolves_per_subagent_policy() -> None:
    mw = SubAgentContextMiddleware(
        default_policy=SubAgentContextPolicy(mode=SubAgentContextMode.ISOLATED),
    )
    mw.register("forking_sub", SubAgentContextPolicy(mode=SubAgentContextMode.FORK, durable_whitelist=("active_plan_ref",)))
    assert mw.policy_for("forking_sub").mode is SubAgentContextMode.FORK
    assert mw.policy_for("other").mode is SubAgentContextMode.ISOLATED

    parent = _parent_state()
    child = mw.prepare_state("forking_sub", parent, "task")
    assert child["messages"][0].content == "task"
    assert "active_plan_ref" in child["_durable_context"]


def test_parent_only_keys_covers_all_noesis_private_state() -> None:
    # Guard against new private-state keys being added without exclusion.
    expected = {
        "_source_revision", "_durable_context", "_file_context",
        "_snip_records", "_micro_compaction_records",
        "_tool_result_replacements", "_tool_catalog_discovered",
        "_summarization_event",
        "messages", "todos", "structured_response",
        "skills_metadata", "skills_load_errors", "memory_contents",
    }
    assert expected <= PARENT_ONLY_KEYS
