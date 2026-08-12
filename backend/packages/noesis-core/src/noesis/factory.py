"""Shared Agent factory: DeepAgents-style flat middleware stack.

The single ReAct Agent assembly entry point. Builds the complete middleware
stack via :func:`noesis.middleware.stack.build_noesis_stack` (design §3) and
calls LangChain ``create_agent()``. ``subagents``/``skills``/``memory``/
``backend``/``interrupt_on`` are mapped to middleware instances inside the
stack builder — they are **not** forwarded to ``create_deep_agent``.

The old five-owner kernel (RuntimeTelemetry / RunGovernor / ContextLifecycle /
ModelExecution / ToolExecution) has been retired; every capability now lives
in a self-contained middleware under ``noesis/middleware/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from deepagents.middleware.async_subagents import AsyncSubAgent
from deepagents.middleware.subagents import CompiledSubAgent, SubAgent
from langchain.agents import create_agent
from langchain.agents.middleware.human_in_the_loop import InterruptOnConfig

from deepagents.backends import BackendProtocol
from noesis.config.env import HitlConfig, ModelConfig
from noesis.llm.factory import get_llm
from noesis.llm.model_limits import resolve_context_max_tokens
from noesis.middleware.stack import NoesisStackDeps, build_noesis_stack


@dataclass(frozen=True)
class MiddlewareInventoryEntry:
    name: str
    category: str
    source: str
    profiles: tuple[str, ...]


def middleware_inventory() -> tuple[MiddlewareInventoryEntry, ...]:
    """Return the declared inventory for the new flat stack.

    Generated from the actual stack assembly for the COMMON_QA profile, so the
    declaration and the builder cannot drift.
    """
    return _INVENTORY


def build_middleware_inventory(
    *,
    profile: str,
    backend: BackendProtocol | None = None,
    subagents: list[SubAgent | CompiledSubAgent] | None = None,
    async_subagents: list[AsyncSubAgent] | None = None,
    extra_middleware: list | None = None,
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None,
    model_id: str | None = None,
) -> list:
    """Build the concrete, ordered stack for inventory/profile assertions."""
    return _build_stack(
        backend=backend,
        profile=profile,
        subagents=subagents,
        async_subagents=async_subagents,
        extra_middleware=extra_middleware,
        interrupt_on=interrupt_on,
        model_id=model_id,
    )


def build_subagent_default_middleware(backend: BackendProtocol) -> list:
    """Sub-agent stack from the same assembly as the primary Agent.

    Sub-agents use the isolated context policy by default (no parent
    conversation / durable ledger inheritance).
    """
    return _build_stack(backend=backend, profile="SUBAGENT")


def build_noesis_runtime_middleware(*, model_id: str | None = None) -> list:
    """Return the public Noesis middleware for the COMMON_QA baseline profile.

    Kept for compatibility with callers that assemble only the runtime layer.
    """
    return _build_stack(profile="COMMON_QA", model_id=model_id)


def _compaction_deps(model_id: str | None = None) -> dict[str, Any]:
    """Resolve the compaction dependencies from model config, when enabled.

    Returns an empty dict (→ CompactionMiddleware omitted) when summarization
    is disabled or the model cannot be resolved without a live config.
    """
    if not getattr(ModelConfig, "summarization_enabled", False):
        return {}
    try:
        model = get_llm(purpose="summarization")
    except Exception:  # noqa: BLE001 — no live config in some environments
        return {}
    max_input = resolve_context_max_tokens(model_id)

    def _summarize(messages: list) -> str:
        # The summary model call is bound here with business tools disabled.
        # The recursion guard in CompactionMiddleware prevents re-entry.
        from langchain_core.messages import get_buffer_string

        prompt = (
            "Summarise the following conversation, preserving: user goals, key "
            "technical decisions, files/code, errors and fixes, rejected "
            "approaches, all user requirements, pending tasks, current work, "
            "and the next step.\n\n"
            f"{get_buffer_string(messages)}"
        )
        from langchain_core.messages import HumanMessage

        return str(model.invoke([HumanMessage(content=prompt)]).content or "")

    def _token_counter(messages: list) -> int:
        return sum(len(repr(m.content)) for m in messages) // 4

    from noesis.middleware import CompactionThresholds

    trigger_tokens = int(getattr(ModelConfig, "summarization_trigger_tokens", 0))
    effective_limit = max_input or 128_000
    reserve = int(getattr(ModelConfig, "summarization_output_reserve", 4_000))
    buffer = int(getattr(ModelConfig, "summarization_transient_buffer", 2_000))
    return {
        "summarize": _summarize,
        "token_counter": _token_counter,
        "compaction_thresholds": CompactionThresholds(
            model_input_limit=effective_limit,
            summary_output_reserve=reserve,
            transient_request_buffer=buffer if trigger_tokens == 0 else max(0, effective_limit - trigger_tokens),
        ),
    }


def _build_stack(
    *,
    backend: BackendProtocol | None = None,
    profile: str = "COMMON_QA",
    subagents: list[SubAgent | CompiledSubAgent] | None = None,
    async_subagents: list[AsyncSubAgent] | None = None,
    extra_middleware: list | None = None,
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None,
    model_id: str | None = None,
) -> list:
    compaction = _compaction_deps(model_id)
    deps = NoesisStackDeps(
        backend=backend,
        profile=profile,
        subagents=subagents,
        async_subagents=async_subagents,
        interrupt_on=interrupt_on if (HitlConfig.enabled and interrupt_on) else None,
        extra_middleware=extra_middleware,
        max_retries=int(getattr(ModelConfig, "max_retries", 0)),
        **compaction,
    )
    return build_noesis_stack(deps)


def create_noesis_agent(
    *,
    system_prompt: str,
    tools: list | None = None,
    checkpointer,
    backend: BackendProtocol | None = None,
    subagents: list[SubAgent | CompiledSubAgent] | None = None,
    async_subagents: list[AsyncSubAgent] | None = None,
    extra_middleware: list | None = None,
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None,
    model=None,
    model_id: str | None = None,
    **create_agent_kwargs: Any,
):
    """创建 Noesis Agent：LangChain ``create_agent`` + 自包含 middleware stack。

    中间件顺序由 ``noesis/middleware/stack.py`` 的 ``build_noesis_stack`` 按设计
    §3 装配。``backend``/``subagents``/``interrupt_on`` 等参数由 factory 映射为
    对应中间件实例加入 stack，不透传给 ``create_deep_agent``。

    Args:
        system_prompt: 系统提示词。
        tools: 额外工具（MCP、RAG 等）；文件系统工具由 ``FilesystemMiddleware`` 注入。
        checkpointer: LangGraph checkpointer。
        backend: 可选文件系统后端；提供时挂载 ``FilesystemMiddleware`` 等。
        subagents: 同步子 Agent 规格，挂载 ``SubAgentMiddleware``（需 ``backend``）。
        async_subagents: 远程 Agent Protocol 异步子 Agent。
        extra_middleware: 额外中间件，追加到 stack 末尾（最内层）。
        interrupt_on: HITL 工具审批配置；仅当 ``HitlConfig.enabled`` 且非空时挂载。
        model: 已解析的 LLM；省略时由 ``model_id`` 解析。
        model_id: 模型目录条目 ID。
        **create_agent_kwargs: 透传给 ``create_agent``（如 ``state_schema``）。
    """
    middleware = _build_stack(
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


# Inventory generated from the COMMON_QA baseline stack assembly. Source of
# truth is ``build_noesis_stack``; this tuple mirrors it for declarative
# assertions and is validated by the stack-assembly contract tests.
_INVENTORY: tuple[MiddlewareInventoryEntry, ...] = (
    MiddlewareInventoryEntry("ToolResultBudgetMiddleware", "context", "Noesis", ("COMMON_QA", "SUPER_AGENT_QA", "FAULT_OPERATION_QA", "SIMPLE_MCP", "SUBAGENT")),
    MiddlewareInventoryEntry("ToolFailureMiddleware", "context", "Noesis", ("COMMON_QA", "SUPER_AGENT_QA", "FAULT_OPERATION_QA", "SIMPLE_MCP", "SUBAGENT")),
    MiddlewareInventoryEntry("FileContextMiddleware", "context", "Noesis", ("SUPER_AGENT_QA", "FAULT_OPERATION_QA", "SUBAGENT")),
    MiddlewareInventoryEntry("SourceRefreshMiddleware", "context", "Noesis", ("COMMON_QA", "SUPER_AGENT_QA", "FAULT_OPERATION_QA", "SIMPLE_MCP", "SUBAGENT")),
    MiddlewareInventoryEntry("DynamicContextMiddleware", "context", "Noesis", ("COMMON_QA", "SUPER_AGENT_QA", "FAULT_OPERATION_QA", "SIMPLE_MCP")),
    MiddlewareInventoryEntry("DurableContextMiddleware", "context", "Noesis", ("SUPER_AGENT_QA", "FAULT_OPERATION_QA")),
    MiddlewareInventoryEntry("SnipMiddleware", "context", "Noesis", ("COMMON_QA", "SUPER_AGENT_QA", "FAULT_OPERATION_QA", "SIMPLE_MCP", "SUBAGENT")),
    MiddlewareInventoryEntry("MicroCompactionMiddleware", "context", "Noesis", ("COMMON_QA", "SUPER_AGENT_QA", "FAULT_OPERATION_QA", "SIMPLE_MCP", "SUBAGENT")),
    MiddlewareInventoryEntry("ToolCatalogMiddleware", "context", "Noesis", ("SUPER_AGENT_QA", "FAULT_OPERATION_QA", "SIMPLE_MCP")),
    MiddlewareInventoryEntry("PatchToolCallsMiddleware", "runtime", "DeepAgents", ("COMMON_QA", "SUPER_AGENT_QA", "FAULT_OPERATION_QA", "SIMPLE_MCP", "SUBAGENT")),
    MiddlewareInventoryEntry("CompactionMiddleware", "context", "Noesis", ("COMMON_QA", "SUPER_AGENT_QA", "FAULT_OPERATION_QA", "SIMPLE_MCP", "SUBAGENT")),
    MiddlewareInventoryEntry("SafeModelRetryMiddleware", "context", "Noesis", ("COMMON_QA", "SUPER_AGENT_QA", "FAULT_OPERATION_QA", "SIMPLE_MCP", "SUBAGENT")),
)
