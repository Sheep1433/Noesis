"""Single DeepAgents-style middleware assembly path for Noesis agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from deepagents.backends import BackendProtocol
from deepagents.middleware.async_subagents import AsyncSubAgent, AsyncSubAgentMiddleware
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.subagents import CompiledSubAgent, SubAgent, SubAgentMiddleware
from langchain.agents.middleware import HumanInTheLoopMiddleware, TodoListMiddleware
from langchain.agents.middleware.human_in_the_loop import InterruptOnConfig
from langchain.agents.middleware.model_call_limit import ModelCallLimitMiddleware
from langchain.agents.middleware.tool_call_limit import ToolCallLimitMiddleware
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.tools import BaseTool

from noesis.agents.middlewares import (
    CompactionMiddleware,
    CompactionThresholds,
    DurableContextMiddleware,
    DynamicContextMiddleware,
    LLMErrorHandlingMiddleware,
    ReadBeforeWriteMiddleware,
    RefreshingMemoryMiddleware,
    RefreshingSkillsMiddleware,
    SessionStatsMiddleware,
    SnipMiddleware,
    ToolFailureMiddleware,
    ToolResultBudgetMiddleware,
)
from noesis.agents.middlewares.compaction_middleware import PRIVATE_STATE_KEYS as _COMPACTION_KEYS
from noesis.agents.middlewares.durable_context_middleware import PRIVATE_STATE_KEYS as _DURABLE_KEYS
from noesis.agents.middlewares.dynamic_context_middleware import PRIVATE_STATE_KEYS as _DYNAMIC_KEYS
from noesis.agents.middlewares.read_before_write_middleware import PRIVATE_STATE_KEYS as _READ_BEFORE_WRITE_KEYS
from noesis.agents.middlewares.refreshing_skills_middleware import PRIVATE_STATE_KEYS as _SKILLS_KEYS
from noesis.agents.middlewares.snip_middleware import PRIVATE_STATE_KEYS as _SNIP_KEYS
from noesis.agents.middlewares.tool_result_budget_middleware import PRIVATE_STATE_KEYS as _TOOL_BUDGET_KEYS

# Subagent isolation must carry each owning middleware's private state across the
# subagent boundary. Aggregated from each middleware's exported key set so that
# renaming a PrivateStateAttr updates isolation automatically.
_PRIVATE_SUBAGENT_KEYS = frozenset(
    _DYNAMIC_KEYS
    + _DURABLE_KEYS
    + _SKILLS_KEYS
    + _READ_BEFORE_WRITE_KEYS
    + _TOOL_BUDGET_KEYS
    + _SNIP_KEYS
    + _COMPACTION_KEYS
)


@dataclass(frozen=True)
class NoesisStackDeps:
    profile: str
    tools: Sequence[BaseTool] = ()
    backend: BackendProtocol | None = None
    dynamic_context_provider: Any = None
    skills_sources: Sequence[str | tuple[str, str]] = ()
    skills_user_id: str | None = None
    skills_system_prompt: str | None = None
    memory_sources: Sequence[str] = ()
    memory_system_prompt: str | None = None
    todo: bool = False
    subagents: Sequence[SubAgent | CompiledSubAgent] = ()
    async_subagents: Sequence[AsyncSubAgent] = ()
    enable_snip: bool = False
    token_counter: Any = None
    request_token_counter: Any = None
    summarize: Any = None
    async_summarize: Any = None
    compaction_thresholds: CompactionThresholds | None = None
    compaction_keep_messages: int = 28
    tool_result_max_chars: int = 24_000
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None
    model_call_limit: int | None = None
    tool_call_limit: int | None = None
    llm_max_retries: int | None = None
    middleware: Sequence[AgentMiddleware] = ()


def build_noesis_stack(deps: NoesisStackDeps) -> list[AgentMiddleware]:
    """Build the complete outer-to-inner stack exactly once."""
    stack: list[AgentMiddleware] = [
        ToolResultBudgetMiddleware(deps.backend, max_chars=deps.tool_result_max_chars),
        ToolFailureMiddleware(),
    ]

    if deps.backend is not None:
        stack.append(ReadBeforeWriteMiddleware(backend=deps.backend))
    if deps.todo:
        stack.append(TodoListMiddleware())
    if deps.skills_sources:
        if deps.backend is None or deps.skills_user_id is None:
            raise ValueError("skills require backend and skills_user_id")
        stack.append(
            RefreshingSkillsMiddleware(
                backend=deps.backend,
                sources=list(deps.skills_sources),
                user_id=deps.skills_user_id,
                system_prompt=deps.skills_system_prompt,
            )
        )
    if deps.backend is not None:
        stack.append(
            FilesystemMiddleware(
                backend=deps.backend,
                tool_token_limit_before_evict=None,
                human_message_token_limit_before_evict=None,
            )
        )
    if deps.subagents:
        if deps.backend is None:
            raise ValueError("subagents require backend")
        stack.append(
            SubAgentMiddleware(
                backend=deps.backend,
                subagents=list(deps.subagents),
                private_state_keys=_PRIVATE_SUBAGENT_KEYS,
            )
        )
    if deps.async_subagents:
        stack.append(AsyncSubAgentMiddleware(async_subagents=list(deps.async_subagents)))
    if deps.memory_sources:
        if deps.backend is None:
            raise ValueError("memory requires backend")
        stack.append(
            RefreshingMemoryMiddleware(
                backend=deps.backend,
                sources=list(deps.memory_sources),
                system_prompt=deps.memory_system_prompt,
            )
        )

    stack.append(DynamicContextMiddleware(deps.dynamic_context_provider))
    if deps.profile in {"SUPER_AGENT_QA", "FAULT_OPERATION_QA"}:
        stack.append(DurableContextMiddleware())
    if deps.enable_snip:
        stack.append(SnipMiddleware(token_counter=deps.token_counter))
    stack.append(PatchToolCallsMiddleware())
    if all(
        value is not None
        for value in (deps.token_counter, deps.summarize, deps.compaction_thresholds)
    ):
        stack.append(
            CompactionMiddleware(
                token_counter=deps.token_counter,
                request_token_counter=deps.request_token_counter,
                summarize=deps.summarize,
                async_summarize=deps.async_summarize,
                thresholds=deps.compaction_thresholds,
                backend=deps.backend,
                keep_messages=deps.compaction_keep_messages,
            )
        )
    if deps.model_call_limit is not None:
        stack.append(ModelCallLimitMiddleware(run_limit=deps.model_call_limit))
    if deps.tool_call_limit is not None:
        stack.append(ToolCallLimitMiddleware(run_limit=deps.tool_call_limit))
    stack.append(LLMErrorHandlingMiddleware(max_retries=deps.llm_max_retries))
    stack.append(SessionStatsMiddleware())
    stack.extend(deps.middleware)
    if deps.interrupt_on:
        stack.append(HumanInTheLoopMiddleware(interrupt_on=deps.interrupt_on))
    return stack


__all__ = ["NoesisStackDeps", "build_noesis_stack"]
