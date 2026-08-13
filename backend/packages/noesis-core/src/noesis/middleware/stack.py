"""New DeepAgents-style middleware stack assembly.

Builds the complete agent middleware stack per ``simplify-agent-context-
architecture`` design §3, using the 12 self-contained Noesis middleware plus
reused upstream middleware (Filesystem / SubAgent / Skills / Memory / Todo /
PatchToolCalls / HITL / call limits). This is the single authoritative
assembly path the factory uses once the old five-owner kernel is retired.

The stack is outer-to-inner; omitting an optional middleware does not change
the relative order of the rest::

    ToolResultBudget  (Noesis)
    → ToolFailure     (Noesis)
    → FileContext     (Noesis)
    → SourceRefresh   (Noesis)
    → TodoList        (LangChain, optional)
    → Skills          (DeepAgents, optional)
    → Filesystem      (DeepAgents, optional)
    → SubAgentContext (Noesis context policy; reuses upstream scheduling)
    → Memory          (DeepAgents, optional)
    → DynamicContext  (Noesis)
    → DurableContext  (Noesis)
    → Snip            (Noesis)
    → MicroCompaction (Noesis)
    → ToolCatalog     (Noesis, when catalog exceeds budget)
    → PatchToolCalls  (DeepAgents)
    → Compaction      (Noesis, composes DeepAgents engine)
    → ModelCallLimit  (LangChain, optional)
    → ToolCallLimit   (LangChain, optional)
    → SafeModelRetry  (Noesis)
    → PromptCaching   (provider adapter, optional)
    → HITL            (LangChain, optional)

Each Noesis middleware is self-contained: it depends only on factory-injected
dependencies and LangGraph typed/private state, and never calls
``noesis.runtime`` / ``noesis.services`` at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.agents.middleware.human_in_the_loop import InterruptOnConfig
from langchain.agents.middleware.model_call_limit import ModelCallLimitMiddleware
from langchain.agents.middleware.tool_call_limit import ToolCallLimitMiddleware
from langchain.agents.middleware.types import AgentMiddleware

from deepagents.backends import BackendProtocol
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.memory import MemoryMiddleware
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from deepagents.middleware.async_subagents import (
    AsyncSubAgent,
    AsyncSubAgentMiddleware,
)
from deepagents.middleware.subagents import CompiledSubAgent, SubAgent, SubAgentMiddleware
from langchain.agents.middleware import TodoListMiddleware

from noesis.middleware import (
    CompactionMiddleware,
    CompactionThresholds,
    DynamicContextMiddleware,
    DurableContextMiddleware,
    FileContextMiddleware,
    MicroCompactionMiddleware,
    SafeModelRetryMiddleware,
    SnipMiddleware,
    SourceRefreshMiddleware,
    SubAgentContextMiddleware,
    SubAgentContextPolicy,
    ToolCatalogMiddleware,
    ToolFailureMiddleware,
    ToolResultBudgetMiddleware,
)

if TYPE_CHECKING:
    from collections.abc import Callable


# design §16 Profile matrix — which Noesis middleware are *required* (always
# bound when the profile is assembled, regardless of provider presence) vs
# optional (bound only when their dependency is present).
_REQUIRED_BY_PROFILE: dict[str, frozenset[str]] = {
    "COMMON_QA": frozenset({
        "SourceRefresh", "DynamicContext", "ToolResultBudget", "Snip",
        "MicroCompaction", "Compaction", "PatchToolCalls", "ToolFailure",
        "SafeModelRetry",
    }),
    "SUPER_AGENT_QA": frozenset({
        "SourceRefresh", "DynamicContext", "ToolResultBudget", "Snip",
        "MicroCompaction", "Compaction", "PatchToolCalls", "ToolFailure",
        "SafeModelRetry", "FileContext", "DurableContext", "ToolCatalog",
    }),
    "FAULT_OPERATION_QA": frozenset({
        "SourceRefresh", "DynamicContext", "ToolResultBudget", "Snip",
        "MicroCompaction", "Compaction", "PatchToolCalls", "ToolFailure",
        "SafeModelRetry", "DurableContext", "ToolCatalog",
    }),
    "SIMPLE_MCP": frozenset({
        "SourceRefresh", "DynamicContext", "ToolResultBudget", "Snip",
        "MicroCompaction", "Compaction", "PatchToolCalls", "ToolFailure",
        "SafeModelRetry", "ToolCatalog",
    }),
    "SUBAGENT": frozenset({
        "SourceRefresh", "ToolResultBudget", "Snip", "MicroCompaction",
        "Compaction", "PatchToolCalls", "ToolFailure", "SafeModelRetry",
    }),
}


def _required(profile: str, name: str) -> bool:
    return name in _REQUIRED_BY_PROFILE.get(profile, frozenset())


@dataclass(frozen=True)
class NoesisStackDeps:
    """Factory-injected dependencies for building the new middleware stack.

    Every field is an already-resolved object or closure — the assembled
    middleware make no runtime calls to resolve these.
    """

    backend: BackendProtocol | None = None
    profile: str = "COMMON_QA"
    # Context providers injected as closures over already-resolved run context.
    dynamic_context_provider: Any = None
    source_fingerprint_provider: Any = None
    tool_catalog_provider: Any = None
    # Compaction dependencies.
    token_counter: Any = None
    summarize: Any = None
    compaction_thresholds: CompactionThresholds | None = None
    # Retry policy.
    max_retries: int = 5
    # Optional upstream capabilities.
    skills_sources: list[str] | None = None
    memory_sources: list[str] | None = None
    subagents: list[SubAgent | CompiledSubAgent] | None = None
    async_subagents: list[AsyncSubAgent] | None = None
    todo: bool = False
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None
    model_call_limit: int | None = None
    tool_call_limit: int | None = None
    subagent_context_policy: SubAgentContextPolicy | None = None
    # Extra explicitly-injected middleware (e.g. project-specific Skills adapter)
    # appended in their declared stack position via :func:`build_noesis_stack`.
    extra_middleware: list[AgentMiddleware] | None = None


def build_noesis_stack(deps: NoesisStackDeps) -> list[AgentMiddleware]:
    """Assemble the full outer-to-inner middleware stack per design §3.

    Noesis middleware required by the profile (design §16 matrix) are always
    bound — provider-less ones bind as no-ops (e.g. SourceRefresh with
    ``source_provider=None`` skips ``before_agent``) until the factory wires a
    real provider. Optional upstream capabilities (Skills/Memory/Todo/HITL/call
    limits/SubAgent) are bound only when their dependency is present;
    omitting one does not change the relative order of the rest.
    """
    stack: list[AgentMiddleware] = []

    # 1. ToolResultBudget (Noesis) — required for all profiles.
    stack.append(ToolResultBudgetMiddleware(deps.backend))

    # 2. ToolFailure (Noesis) — required for all profiles.
    stack.append(ToolFailureMiddleware())

    # 3. FileContext (Noesis) — profile-required (SUPER/FAULT/SUBAGENT) when a
    #    filesystem backend exists; otherwise omitted.
    if deps.backend is not None and _required(deps.profile, "FileContext"):
        stack.append(FileContextMiddleware())

    # 4. SourceRefresh (Noesis) — profile-required; binds with provider=None
    #    (no-op: before_agent returns None) until a fingerprint provider is
    #    wired by the factory.
    if _required(deps.profile, "SourceRefresh"):
        stack.append(SourceRefreshMiddleware(source_provider=deps.source_fingerprint_provider))

    # 5. TodoList (LangChain, optional).
    if deps.todo:
        stack.append(TodoListMiddleware())

    # 6. Skills (DeepAgents, optional) — requires a backend to read skill files.
    if deps.skills_sources and deps.backend is not None:
        stack.append(SkillsMiddleware(backend=deps.backend, sources=deps.skills_sources))

    # 7. Filesystem (DeepAgents, optional).
    if deps.backend is not None:
        stack.append(FilesystemMiddleware(backend=deps.backend))

    # 8. SubAgentContext (Noesis context policy) + upstream SubAgent scheduling.
    #    SubAgentContextMiddleware holds the per-subagent policy registry;
    #    the upstream SubAgentMiddleware handles compile/schedule/result-return
    #    and is wired separately by the factory into the task tool.
    has_subagent_policy = deps.subagent_context_policy is not None
    has_subagents = bool(deps.subagents)
    if has_subagent_policy or has_subagents:
        stack.append(SubAgentContextMiddleware(default_policy=deps.subagent_context_policy))
    if has_subagents:
        if deps.backend is None:
            raise ValueError("SubAgentMiddleware requires `backend` when `subagents` is set")
        stack.append(SubAgentMiddleware(backend=deps.backend, subagents=deps.subagents))
    if deps.async_subagents:
        stack.append(AsyncSubAgentMiddleware(async_subagents=deps.async_subagents))

    # 9. Memory (DeepAgents, optional) — requires a backend to read memory files.
    if deps.memory_sources and deps.backend is not None:
        stack.append(MemoryMiddleware(backend=deps.backend, sources=deps.memory_sources))

    # 10. DynamicContext (Noesis) — required for non-SUBAGENT profiles.
    if _required(deps.profile, "DynamicContext"):
        stack.append(DynamicContextMiddleware(context_provider=deps.dynamic_context_provider))

    # 11. DurableContext (Noesis) — profile-required (SUPER/FAULT) only.
    if _required(deps.profile, "DurableContext"):
        stack.append(DurableContextMiddleware())

    # 12. Snip (Noesis) — required for all profiles.
    stack.append(SnipMiddleware())

    # 13. MicroCompaction (Noesis) — required; backend optional (text fallback).
    stack.append(MicroCompactionMiddleware(deps.backend))

    # 14. ToolCatalog (Noesis) — profile-required; binds with provider=None
    #    (no-op: no deferred filtering) until a catalog provider is wired.
    if _required(deps.profile, "ToolCatalog"):
        stack.append(ToolCatalogMiddleware(catalog_provider=deps.tool_catalog_provider))

    # 15. PatchToolCalls (DeepAgents) — required for all profiles.
    stack.append(PatchToolCallsMiddleware())

    # 16. Compaction (Noesis) — profile-required, but needs summarize +
    #    token_counter + thresholds. When summarization is disabled (no live
    #    model config), the middleware is omitted rather than bound as a no-op,
    #    because there is no safe no-op for a summarisation call.
    if (
        _required(deps.profile, "Compaction")
        and deps.summarize is not None
        and deps.token_counter is not None
        and deps.compaction_thresholds is not None
    ):
        stack.append(
            CompactionMiddleware(
                token_counter=deps.token_counter,
                summarize=deps.summarize,
                thresholds=deps.compaction_thresholds,
                backend=deps.backend,
            ),
        )

    # 17. ModelCallLimit (LangChain, optional).
    if deps.model_call_limit is not None:
        stack.append(ModelCallLimitMiddleware(run_limit=deps.model_call_limit))

    # 18. ToolCallLimit (LangChain, optional).
    if deps.tool_call_limit is not None:
        stack.append(ToolCallLimitMiddleware(run_limit=deps.tool_call_limit))

    # 19. SafeModelRetry (Noesis) — transient HTTP error retry (408/429/5xx,
    #     timeout, connection); ContextOverflowError routes to Compaction.
    stack.append(SafeModelRetryMiddleware(max_retries=deps.max_retries))

    # 20. HITL (LangChain, optional).
    if deps.interrupt_on:
        stack.append(HumanInTheLoopMiddleware(interrupt_on=deps.interrupt_on))

    # Extra middleware are appended at the end (innermost-after-HITL) by
    # default; callers requiring a specific position should construct the
    # stack explicitly. Kept for backward compatibility during migration.
    if deps.extra_middleware:
        stack.extend(deps.extra_middleware)

    return stack


__all__ = ["NoesisStackDeps", "build_noesis_stack"]
