"""Assembly contracts for the new Noesis middleware stack (design §3).

These tests pin the exact outer-to-inner order of the assembled stack and the
optional-middleware inclusion rules. They do not start a live agent — they
verify the assembly path is deterministic and self-contained.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from noesis.middleware.stack import NoesisStackDeps, build_noesis_stack


@dataclass
class _FakeWriteResult:
    error: str | None = None


class _FakeBackend:
    """Minimal BackendProtocol stub — only `write` is exercised by assembly."""

    def write(self, path: str, content: str) -> _FakeWriteResult:
        return _FakeWriteResult(error=None)


def _names(stack) -> list[str]:
    return [type(m).__name__ for m in stack]


def test_minimal_stack_has_required_noesis_middleware() -> None:
    """Even with no optional deps, the required Noesis middleware appear."""
    deps = NoesisStackDeps(
        summarize=lambda msgs: "s",
        token_counter=lambda msgs: 10,
        compaction_thresholds=__import__(
            "noesis.middleware.compaction_middleware", fromlist=["CompactionThresholds"],
        ).CompactionThresholds(model_input_limit=100, summary_output_reserve=10, transient_request_buffer=10),
    )
    stack = build_noesis_stack(deps)
    names = _names(stack)
    # COMMON_QA required Noesis middleware present (design §16).
    for required in (
        "ToolResultBudgetMiddleware",
        "ToolFailureMiddleware",
        "SourceRefreshMiddleware",
        "DynamicContextMiddleware",
        "SnipMiddleware",
        "MicroCompactionMiddleware",
        "CompactionMiddleware",
        "PatchToolCallsMiddleware",
        "SafeModelRetryMiddleware",
    ):
        assert required in names, f"{required} missing from minimal stack"
    # COMMON_QA does NOT include DurableContext/ToolCatalog/FileContext.
    assert "DurableContextMiddleware" not in names
    assert "ToolCatalogMiddleware" not in names
    assert "FileContextMiddleware" not in names


def test_full_stack_exact_order_per_design() -> None:
    from unittest.mock import MagicMock
    from deepagents.middleware.subagents import CompiledSubAgent

    sub_spec: CompiledSubAgent = {
        "name": "worker",
        "description": "d",
        "runnable": MagicMock(name="compiled_subagent"),
    }
    deps = NoesisStackDeps(
        backend=_FakeBackend(),
        profile="SUPER_AGENT_QA",
        dynamic_context_provider=lambda: None,
        source_fingerprint_provider=lambda: None,
        tool_catalog_provider=lambda: [],
        summarize=lambda msgs: "s",
        token_counter=lambda msgs: 10,
        compaction_thresholds=__import__(
            "noesis.middleware.compaction_middleware", fromlist=["CompactionThresholds"],
        ).CompactionThresholds(model_input_limit=100, summary_output_reserve=10, transient_request_buffer=10),
        skills_sources=["/skills"],
        memory_sources=["/memory"],
        subagents=[sub_spec],
        todo=True,
        interrupt_on={"edit_file": True},
        model_call_limit=50,
        tool_call_limit=100,
        retry_on=(RuntimeError,),
    )
    stack = build_noesis_stack(deps)
    expected = [
        "ToolResultBudgetMiddleware",
        "ToolFailureMiddleware",
        "FileContextMiddleware",
        "SourceRefreshMiddleware",
        "TodoListMiddleware",
        "SkillsMiddleware",
        "FilesystemMiddleware",
        "SubAgentContextMiddleware",
        "SubAgentMiddleware",
        "MemoryMiddleware",
        "DynamicContextMiddleware",
        "DurableContextMiddleware",
        "SnipMiddleware",
        "MicroCompactionMiddleware",
        "ToolCatalogMiddleware",
        "PatchToolCallsMiddleware",
        "CompactionMiddleware",
        "ModelCallLimitMiddleware",
        "ToolCallLimitMiddleware",
        "SafeModelRetryMiddleware",
        "HumanInTheLoopMiddleware",
    ]
    assert _names(stack) == expected


def test_optional_middleware_omission_preserves_relative_order() -> None:
    """Removing optional middleware keeps the required ones in the same order."""
    deps = NoesisStackDeps(
        summarize=lambda msgs: "s",
        token_counter=lambda msgs: 10,
        compaction_thresholds=__import__(
            "noesis.middleware.compaction_middleware", fromlist=["CompactionThresholds"],
        ).CompactionThresholds(model_input_limit=100, summary_output_reserve=10, transient_request_buffer=10),
    )
    stack = build_noesis_stack(deps)
    names = _names(stack)
    # No optional middleware present.
    assert "FilesystemMiddleware" not in names
    assert "SkillsMiddleware" not in names
    assert "TodoListMiddleware" not in names
    assert "SubAgentMiddleware" not in names
    assert "MemoryMiddleware" not in names
    assert "HumanInTheLoopMiddleware" not in names
    # Required Noesis still in relative order (COMMON_QA baseline).
    required = [
        "ToolResultBudgetMiddleware",
        "ToolFailureMiddleware",
        "SourceRefreshMiddleware",
        "DynamicContextMiddleware",
        "SnipMiddleware",
        "MicroCompactionMiddleware",
        "PatchToolCallsMiddleware",
        "CompactionMiddleware",
        "SafeModelRetryMiddleware",
    ]
    indices = [names.index(r) for r in required]
    assert indices == sorted(indices)


def test_subagents_require_backend() -> None:
    from unittest.mock import MagicMock
    from deepagents.middleware.subagents import CompiledSubAgent

    spec: CompiledSubAgent = {
        "name": "x",
        "description": "d",
        "runnable": MagicMock(name="compiled"),
    }
    deps = NoesisStackDeps(subagents=[spec], summarize=lambda m: "s", token_counter=lambda m: 1)
    with pytest.raises(ValueError, match="backend"):
        build_noesis_stack(deps)


def test_compaction_omitted_without_all_dependencies() -> None:
    # summarize present but no token_counter → Compaction omitted, no crash.
    deps = NoesisStackDeps(summarize=lambda m: "s")
    stack = build_noesis_stack(deps)
    assert "CompactionMiddleware" not in _names(stack)


def test_stack_is_a_list_of_distinct_instances() -> None:
    deps = NoesisStackDeps(
        summarize=lambda m: "s",
        token_counter=lambda m: 1,
        compaction_thresholds=__import__(
            "noesis.middleware.compaction_middleware", fromlist=["CompactionThresholds"],
        ).CompactionThresholds(model_input_limit=100, summary_output_reserve=10, transient_request_buffer=10),
    )
    stack = build_noesis_stack(deps)
    assert len(stack) == len(set(id(m) for m in stack))
