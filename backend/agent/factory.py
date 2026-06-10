"""Shared agent factory: unified ``create_noesis_agent`` + runtime guards."""

from __future__ import annotations

from typing import Any, Literal, cast

from deepagents.middleware.async_subagents import AsyncSubAgent, AsyncSubAgentMiddleware
from deepagents.middleware.subagents import CompiledSubAgent, SubAgent, SubAgentMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import (
    ClearToolUsesEdit,
    ContextEditingMiddleware,
    ToolCallLimitMiddleware,
)
from langchain.agents.middleware.types import AgentMiddleware

from deepagents.backends import BackendProtocol
from deepagents.middleware.filesystem import FilesystemMiddleware
from agent.middlewares import (
    DanglingToolCallMiddleware,
    LoopDetectionMiddleware,
    SessionClockMiddleware,
    ToolErrorHandlingMiddleware,
    create_summary_offload_middleware,
)
from config.env import ModelConfig
from llm import get_llm

ExitBehavior = Literal["continue", "error", "end"]

# 中间件顺序（能力层 + extra + 运行时防护 + ToolCallLimit 尾栈）:
#   Filesystem → SubAgent → AsyncSubAgent → extra_middleware
#   → SessionClock → runtime guards → ToolCallLimit
# Skills 等能力由调用方通过 extra_middleware 自行挂载。


def build_subagent_default_middleware(
    backend: BackendProtocol,
) -> list[AgentMiddleware]:
    """子 Agent 默认中间件栈（Filesystem + 运行时防护）。"""
    stack: list[AgentMiddleware] = [FilesystemMiddleware(backend=backend)]
    stack.extend(build_noesis_runtime_middleware(include_tool_call_limits=False))
    return stack


def build_noesis_runtime_middleware(
    *,
    include_tool_call_limits: bool = True,
) -> list[AgentMiddleware]:
    """Noesis 运行时防护中间件（clock → repair → offload → context → loop → limit）。"""
    middleware: list[AgentMiddleware] = [SessionClockMiddleware()]

    if ModelConfig.dangling_tool_call_repair_enabled:
        middleware.append(DanglingToolCallMiddleware())

    summary_middleware = create_summary_offload_middleware()
    if summary_middleware is not None:
        middleware.append(summary_middleware)

    middleware.append(
        ContextEditingMiddleware(
            edits=[
                ClearToolUsesEdit(trigger=10000),
            ],
        )
    )

    if ModelConfig.loop_detection_enabled:
        middleware.append(
            LoopDetectionMiddleware(
                warn_threshold=ModelConfig.loop_detection_warn_threshold,
                hard_limit=ModelConfig.loop_detection_hard_limit,
            )
        )

    middleware.append(ToolErrorHandlingMiddleware())

    if include_tool_call_limits:
        middleware.extend(build_tool_call_limit_middleware())

    return middleware


def build_tool_call_limit_middleware() -> list[AgentMiddleware]:
    """ToolCallLimit 尾栈：全局 thread/run 限制 + task 委派 per-run 限制。"""
    if not ModelConfig.tool_call_limit_enabled:
        return []

    exit_behavior = cast(
        ExitBehavior,
        ModelConfig.tool_call_limit_exit_behavior,
    )
    limits: list[AgentMiddleware] = []

    thread_limit = ModelConfig.tool_call_limit_thread_limit
    run_limit = ModelConfig.tool_call_limit_run_limit
    if thread_limit is not None or run_limit is not None:
        limits.append(
            ToolCallLimitMiddleware(
                thread_limit=thread_limit,
                run_limit=run_limit,
                exit_behavior=exit_behavior,
            )
        )

    task_run_limit = ModelConfig.tool_call_limit_task_run_limit
    if task_run_limit is not None:
        limits.append(
            ToolCallLimitMiddleware(
                tool_name="task",
                run_limit=task_run_limit,
                exit_behavior=exit_behavior,
            )
        )

    return limits


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


def create_noesis_agent(
    *,
    system_prompt: str,
    tools: list | None = None,
    checkpointer,
    backend: BackendProtocol | None = None,
    subagents: list[SubAgent | CompiledSubAgent] | None = None,
    async_subagents: list[AsyncSubAgent] | None = None,
    extra_middleware: list[AgentMiddleware] | None = None,
    **create_agent_kwargs: Any,
):
    """创建 Noesis Agent：LangChain ``create_agent`` + 能力中间件 + 运行时防护。

    中间件顺序::

        FilesystemMiddleware (optional)
        → SubAgentMiddleware (optional)
        → AsyncSubAgentMiddleware (optional)
        → extra_middleware（如 SkillsMiddleware，由调用方按需传入）
        → SessionClockMiddleware
        → DanglingToolCall / SummarizationOffload / ContextEditing / LoopDetection
        → ToolErrorHandlingMiddleware
        → ToolCallLimitMiddleware (optional, tail)

    Args:
        system_prompt: 系统提示词。
        tools: 额外工具（MCP、RAG 等）；文件系统工具由 ``FilesystemMiddleware`` 注入。
        checkpointer: LangGraph checkpointer。
        backend: 可选文件系统后端；提供时挂载 ``FilesystemMiddleware``。
        subagents: 同步子 Agent 规格，挂载 ``SubAgentMiddleware``（需 ``backend``）。
        async_subagents: 远程 Agent Protocol 异步子 Agent，挂载 ``AsyncSubAgentMiddleware``。
        extra_middleware: 能力扩展中间件（Skills 等），插在文件系统/子 Agent 栈与运行时防护之间。
        **create_agent_kwargs: 透传给 ``create_agent``（如 ``state_schema``）。
    """
    middleware: list[AgentMiddleware] = []
    middleware.extend(
        _build_capability_middleware(
            backend,
            subagents=subagents,
            async_subagents=async_subagents,
        )
    )
    if extra_middleware:
        middleware.extend(extra_middleware)
    middleware.extend(build_noesis_runtime_middleware())

    return create_agent(
        model=get_llm(),
        tools=tools or [],
        system_prompt=system_prompt,
        checkpointer=checkpointer,
        middleware=middleware,
        **create_agent_kwargs,
    )
