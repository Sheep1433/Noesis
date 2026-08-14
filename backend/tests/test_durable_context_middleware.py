"""Contracts for bounded, private durable context state."""

from __future__ import annotations

from typing import get_args, get_type_hints

from langchain.agents import create_agent
from langchain.agents.middleware.types import ModelRequest, PrivateStateAttr
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from noesis.agents.middlewares.durable_context_middleware import (
    MAX_COMPACT_INSTRUCTION_CHARS,
    MAX_DURABLE_REFS,
    MAX_DURABLE_REF_CHARS,
    DurableContextMiddleware,
    DurableContextState,
    normalize_durable_context,
)


def _request(state: dict) -> ModelRequest:
    return ModelRequest(
        model=object(),  # type: ignore[arg-type]
        messages=[],
        system_message=SystemMessage(content="sys"),
        state=state,
    )


def _invoke(middleware: DurableContextMiddleware, state: dict) -> str:
    seen: list[ModelRequest] = []
    middleware.wrap_model_call(_request(state), lambda request: seen.append(request))  # type: ignore[arg-type,return-value]
    return seen[0].system_message.text


def test_state_schema_marks_durable_context_private() -> None:
    assert DurableContextMiddleware.state_schema is DurableContextState
    hint = get_type_hints(DurableContextState, include_extras=True)["durable_context"]
    assert PrivateStateAttr in get_args(get_args(hint)[0])


def test_langchain_graph_accepts_schema_and_omits_private_state_from_output() -> None:
    agent = create_agent(
        model=FakeListChatModel(responses=["ok"]),
        tools=[],
        middleware=[DurableContextMiddleware()],
    )

    result = agent.invoke({"messages": [HumanMessage(content="hello")]})

    assert "durable_context" not in result


def test_empty_state_does_not_inject_block() -> None:
    assert _invoke(DurableContextMiddleware(), {"messages": []}) == "sys"


def test_typed_state_injects_all_reference_categories() -> None:
    state = {
        "messages": [],
        "durable_context": {
            "active_plan_ref": "PLAN-1",
            "pending_tasks": ["write tests"],
            "delegation_ledger": ["researcher→/artifacts/r1.md"],
            "loaded_skill_refs": ["skill-a"],
            "active_file_refs": ["/repo/a.py"],
            "discovered_tool_refs": ["mcp.search"],
            "user_compact_instructions": "keep the migration table",
        },
    }

    text = _invoke(DurableContextMiddleware(), state)

    assert "## Durable Context" in text
    assert "active_plan: PLAN-1" in text
    assert "pending_tasks: write tests" in text
    assert "delegation_ledger: researcher→/artifacts/r1.md" in text
    assert "loaded_skills: skill-a" in text
    assert "active_files: /repo/a.py" in text
    assert "discovered_tools: mcp.search" in text
    assert "compact_instructions: keep the migration table" in text


def test_before_agent_persists_only_bounded_deduplicated_refs() -> None:
    refs = ["same", "same", *(f"ref-{index}" for index in range(MAX_DURABLE_REFS + 10))]
    long_ref = "x" * (MAX_DURABLE_REF_CHARS + 20)
    long_instructions = "i" * (MAX_COMPACT_INSTRUCTION_CHARS + 20)
    state = {
        "messages": [],
        "durable_context": {
            "active_plan_ref": long_ref,
            "pending_tasks": refs,
            "active_file_refs": [long_ref],
            "user_compact_instructions": long_instructions,
        },
    }

    update = DurableContextMiddleware().before_agent(state, runtime=None)  # type: ignore[arg-type]

    assert update is not None
    context = update["durable_context"]
    assert len(context["pending_tasks"]) == MAX_DURABLE_REFS
    assert context["pending_tasks"].count("same") == 1
    assert len(context["active_plan_ref"]) == MAX_DURABLE_REF_CHARS
    assert context["active_plan_ref"].endswith("…")
    assert len(context["active_file_refs"][0]) == MAX_DURABLE_REF_CHARS
    assert len(context["user_compact_instructions"]) == MAX_COMPACT_INSTRUCTION_CHARS


def test_normalized_state_is_stable_at_run_boundary() -> None:
    context = normalize_durable_context(
        {
            "active_plan_ref": "PLAN",
            "pending_tasks": ["one", "two"],
        },
    )
    state = {"messages": [], "durable_context": context}

    assert DurableContextMiddleware().before_agent(state, runtime=None) is None  # type: ignore[arg-type]


def test_model_hook_does_not_mutate_state() -> None:
    context = {"active_plan_ref": "PLAN", "pending_tasks": ["one"]}
    state = {"messages": [], "durable_context": context}

    _invoke(DurableContextMiddleware(), state)

    assert state["durable_context"] is context
    assert context == {"active_plan_ref": "PLAN", "pending_tasks": ["one"]}


def test_middleware_exposes_no_ad_hoc_public_mutation_api() -> None:
    public_mutators = {
        "set_active_plan",
        "add_task",
        "complete_task",
        "record_delegation",
        "set_compact_instructions",
        "merge_refs",
        "snapshot",
    }
    assert public_mutators.isdisjoint(DurableContextMiddleware.__dict__)


def test_durable_block_rebuilds_after_conversation_compaction() -> None:
    state = {
        "messages": ["full conversation"],
        "durable_context": {"active_plan_ref": "PLAN-X"},
    }
    state["messages"] = ["[summary]"]

    assert "active_plan: PLAN-X" in _invoke(DurableContextMiddleware(), state)
