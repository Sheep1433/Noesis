"""Shared Agent factory and the auditable runtime middleware inventory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from deepagents.middleware.async_subagents import AsyncSubAgent, AsyncSubAgentMiddleware
from deepagents.middleware.subagents import CompiledSubAgent, SubAgent, SubAgentMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.agents.middleware.human_in_the_loop import InterruptOnConfig
from langchain.agents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware.types import AgentMiddleware

from deepagents.backends import BackendProtocol
from deepagents.middleware.filesystem import FilesystemMiddleware
from noesis.config.env import HitlConfig, ModelConfig
from noesis.llm.factory import get_llm
from noesis.llm.model_limits import resolve_context_max_tokens
from noesis.agents.middlewares.kernel.context_lifecycle_middleware import ContextLifecycleMiddleware
from noesis.agents.middlewares.kernel.context_metrics import get_agent_token_counter
from noesis.agents.middlewares.kernel.model_execution_middleware import ModelExecutionMiddleware
from noesis.agents.middlewares.kernel.run_governor_middleware import RunGovernorMiddleware
from noesis.agents.middlewares.kernel.runtime_telemetry_middleware import RuntimeTelemetryMiddleware
from noesis.agents.middlewares.kernel.tool_execution_middleware import ToolExecutionMiddleware


# Actual order is outer-to-inner and is intentionally kept in one builder:
# Telemetry → ToolExecution → capabilities → HITL → Governor → Context → Model.


@dataclass(frozen=True)
class MiddlewareInventoryEntry:
    name: str
    category: str
    source: str
    profiles: tuple[str, ...]


MIDDLEWARE_INVENTORY: tuple[MiddlewareInventoryEntry, ...] = (
    MiddlewareInventoryEntry("RuntimeTelemetryMiddleware", "kernel", "Noesis", ("COMMON_QA", "SUPER_AGENT_QA", "FAULT_OPERATION_QA", "SIMPLE_MCP", "SUBAGENT")),
    MiddlewareInventoryEntry("ToolExecutionMiddleware", "kernel", "Noesis", ("COMMON_QA", "SUPER_AGENT_QA", "FAULT_OPERATION_QA", "SIMPLE_MCP", "SUBAGENT")),
    MiddlewareInventoryEntry("FilesystemMiddleware", "capability", "DeepAgents", ("SUPER_AGENT_QA", "FAULT_OPERATION_QA", "SUBAGENT")),
    MiddlewareInventoryEntry("SubAgentMiddleware", "capability", "DeepAgents", ("SUPER_AGENT_QA", "FAULT_OPERATION_QA")),
    MiddlewareInventoryEntry("AsyncSubAgentMiddleware", "capability", "DeepAgents", ("SUPER_AGENT_QA",)),
    MiddlewareInventoryEntry("TodoListMiddleware", "capability", "LangChain", ("SUPER_AGENT_QA",)),
    MiddlewareInventoryEntry("HumanInTheLoopMiddleware", "capability", "LangChain", ("SUPER_AGENT_QA", "FAULT_OPERATION_QA")),
    MiddlewareInventoryEntry("VersionedSkillsMiddleware", "adapter", "Noesis", ("SUPER_AGENT_QA", "SUBAGENT")),
    MiddlewareInventoryEntry("TurnMemoryMiddleware", "adapter", "Noesis", ("SUPER_AGENT_QA",)),
    MiddlewareInventoryEntry("RunGovernorMiddleware", "kernel", "Noesis", ("COMMON_QA", "SUPER_AGENT_QA", "FAULT_OPERATION_QA", "SIMPLE_MCP", "SUBAGENT")),
    MiddlewareInventoryEntry("ContextLifecycleMiddleware", "kernel", "Noesis", ("COMMON_QA", "SUPER_AGENT_QA", "FAULT_OPERATION_QA", "SIMPLE_MCP", "SUBAGENT")),
    MiddlewareInventoryEntry("ModelExecutionMiddleware", "kernel", "Noesis", ("COMMON_QA", "SUPER_AGENT_QA", "FAULT_OPERATION_QA", "SIMPLE_MCP", "SUBAGENT")),
)


def middleware_inventory() -> tuple[MiddlewareInventoryEntry, ...]:
    return MIDDLEWARE_INVENTORY


def build_middleware_inventory(
    *,
    profile: str,
    backend: BackendProtocol | None = None,
    subagents: list[SubAgent | CompiledSubAgent] | None = None,
    async_subagents: list[AsyncSubAgent] | None = None,
    extra_middleware: list[AgentMiddleware] | None = None,
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None,
    model_id: str | None = None,
) -> list[AgentMiddleware]:
    """Build the concrete, ordered stack for inventory/profile assertions."""
    return _build_middleware_stack(
        backend=backend,
        profile=profile,
        subagents=subagents,
        async_subagents=async_subagents,
        extra_middleware=extra_middleware,
        interrupt_on=interrupt_on,
        model_id=model_id,
    )


def _build_context_compaction_engine(model_id: str | None = None):
    """Use LangChain's summarizer as an engine, never as a second decision owner."""
    if not getattr(ModelConfig, "summarization_enabled", False):
        return None
    model = get_llm(purpose="summarization")
    max_input = resolve_context_max_tokens(model_id)
    if not getattr(model, "profile", None):
        model.profile = {"max_input_tokens": max_input}
    trigger_tokens = int(getattr(ModelConfig, "summarization_trigger_tokens", 0))
    trigger = (
        ("tokens", trigger_tokens)
        if trigger_tokens > 0
        else ("fraction", getattr(ModelConfig, "summarization_trigger_fraction", 0.75))
    )
    return SummarizationMiddleware(
        model,
        trigger=trigger,
        keep=("messages", int(getattr(ModelConfig, "summarization_messages_to_keep", 28))),
        token_counter=get_agent_token_counter(),
    )


def build_subagent_default_middleware(
    backend: BackendProtocol,
) -> list[AgentMiddleware]:
    """Sub-agent stack from the same inventory as the primary Agent."""
    return _build_middleware_stack(backend=backend, profile="SUBAGENT")


def build_noesis_runtime_middleware(
    *,
    model_id: str | None = None,
) -> list[AgentMiddleware]:
    """Return only the public kernel; capability construction is separate."""
    return [
        RuntimeTelemetryMiddleware(enabled=ModelConfig.context_display_enabled),
        ToolExecutionMiddleware(),
        RunGovernorMiddleware(),
        ContextLifecycleMiddleware(model_id=model_id, compaction_engine=_build_context_compaction_engine(model_id)),
        ModelExecutionMiddleware(max_retries=getattr(ModelConfig, "max_retries", 0)),
    ]


def _build_capability_middleware(
    backend: BackendProtocol | None,
    *,
    subagents: list[SubAgent | CompiledSubAgent] | None = None,
    async_subagents: list[AsyncSubAgent] | None = None,
) -> list[AgentMiddleware]:
    stack: list[AgentMiddleware] = []

    if backend is not None:
        stack.append(FilesystemMiddleware(backend=backend))

    if subagents:
        if backend is None:
            raise ValueError("SubAgentMiddleware requires `backend` when `subagents` is set")
        stack.append(
            SubAgentMiddleware(
                backend=cast(Any, backend),
                subagents=subagents,
            )
        )

    if async_subagents:
        stack.append(AsyncSubAgentMiddleware(async_subagents=async_subagents))

    return stack


def _build_middleware_stack(
    *,
    backend: BackendProtocol | None,
    profile: str,
    subagents: list[SubAgent | CompiledSubAgent] | None = None,
    async_subagents: list[AsyncSubAgent] | None = None,
    extra_middleware: list[AgentMiddleware] | None = None,
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None,
    model_id: str | None = None,
) -> list[AgentMiddleware]:
    capabilities = _build_capability_middleware(backend, subagents=subagents, async_subagents=async_subagents)
    capabilities.extend(extra_middleware or [])
    stack: list[AgentMiddleware] = [
        RuntimeTelemetryMiddleware(enabled=ModelConfig.context_display_enabled),
        ToolExecutionMiddleware(),
        *capabilities,
    ]
    if HitlConfig.enabled and interrupt_on:
        stack.append(HumanInTheLoopMiddleware(interrupt_on=interrupt_on))
    stack.extend([
        RunGovernorMiddleware(),
        ContextLifecycleMiddleware(model_id=model_id, compaction_engine=_build_context_compaction_engine(model_id)),
        ModelExecutionMiddleware(max_retries=getattr(ModelConfig, "max_retries", 0)),
    ])
    if profile != "UNKNOWN":
        allowed = {
            entry.name
            for entry in MIDDLEWARE_INVENTORY
            if profile in entry.profiles
        }
        unexpected = [type(item).__name__ for item in stack if type(item).__name__ not in allowed]
        if unexpected:
            raise ValueError(
                f"middleware not declared for profile {profile}: {', '.join(unexpected)}"
            )
    return stack


def create_noesis_agent(
    *,
    system_prompt: str,
    tools: list | None = None,
    checkpointer,
    backend: BackendProtocol | None = None,
    subagents: list[SubAgent | CompiledSubAgent] | None = None,
    async_subagents: list[AsyncSubAgent] | None = None,
    extra_middleware: list[AgentMiddleware] | None = None,
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None,
    model=None,
    model_id: str | None = None,
    **create_agent_kwargs: Any,
):
    """创建 Noesis Agent：LangChain ``create_agent`` + 能力中间件 + runtime kernel。

    中间件顺序::

        RuntimeTelemetry → ToolExecution → capabilities → HITL
        → RunGovernor → ContextLifecycle → ModelExecution

    Args:
        system_prompt: 系统提示词。
        tools: 额外工具（MCP、RAG 等）；文件系统工具由 ``FilesystemMiddleware`` 注入。
        checkpointer: LangGraph checkpointer。
        backend: 可选文件系统后端；提供时挂载 ``FilesystemMiddleware``。
        subagents: 同步子 Agent 规格，挂载 ``SubAgentMiddleware``（需 ``backend``）。
        async_subagents: 远程 Agent Protocol 异步子 Agent，挂载 ``AsyncSubAgentMiddleware``。
        extra_middleware: 能力扩展中间件（Skills 等），插在文件系统/子 Agent 栈与运行时防护之间。
        interrupt_on: HITL 工具审批配置；仅当 ``HitlConfig.enabled`` 且非空时挂载中间件。
        **create_agent_kwargs: 透传给 ``create_agent``（如 ``state_schema``）。
    """
    middleware = _build_middleware_stack(
        backend=backend,
        profile=create_agent_kwargs.pop("profile", "UNKNOWN"),
        subagents=subagents,
        async_subagents=async_subagents,
        extra_middleware=extra_middleware,
        interrupt_on=interrupt_on,
        model_id=model_id,
    )

    return create_agent(
        model=model if model is not None else get_llm(model_id=model_id),
        tools=tools or [],
        system_prompt=system_prompt,
        checkpointer=checkpointer,
        middleware=middleware,
        **create_agent_kwargs,
    )
