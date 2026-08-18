"""Single Agent factory for every Noesis ReAct profile."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from deepagents.backends import BackendProtocol
from deepagents.middleware.async_subagents import AsyncSubAgent
from deepagents.middleware.subagents import CompiledSubAgent, SubAgent
from langchain.agents import create_agent
from langchain.agents.middleware.human_in_the_loop import InterruptOnConfig
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.tools import BaseTool

from noesis.agents.middlewares import (
    CompactionThresholds,
    DynamicContextBlock,
    DynamicContextProvider,
)
from noesis.agents.middlewares.stack import NoesisStackDeps, build_noesis_stack
from noesis.config.env import HitlConfig, ModelConfig
from noesis.llm.factory import get_llm
from noesis.llm.model_limits import resolve_context_max_tokens


@dataclass(frozen=True)
class MiddlewareInventoryEntry:
    name: str
    source: str
    order: int


def middleware_inventory(stack: Sequence[AgentMiddleware]) -> tuple[MiddlewareInventoryEntry, ...]:
    """Describe the actual instances that will be passed to create_agent."""
    entries = []
    for order, middleware in enumerate(stack):
        module = type(middleware).__module__
        source = "Noesis" if module.startswith("noesis.") else "DeepAgents" if module.startswith("deepagents.") else "LangChain"
        entries.append(MiddlewareInventoryEntry(type(middleware).__name__, source, order))
    return tuple(entries)


def _compaction_deps(model: Any, model_id: str | None) -> dict[str, Any]:
    if not ModelConfig.summarization_enabled:
        return {}
    summary_model = get_llm(purpose="summarization")
    model_limit = resolve_context_max_tokens(model_id) or ModelConfig.context_max_input_tokens
    trigger = ModelConfig.summarization_trigger_tokens
    reserve = max(1, min(20_000, ModelConfig.max_tokens))
    effective_limit = max(1, model_limit - reserve)
    if trigger > 0:
        # 绝对 token 触发：transient 是距 effective_limit 顶部的余量
        transient = max(1, effective_limit - trigger)
    else:
        # 比例触发（对齐 hermes compression.threshold）：
        # trigger_fraction 表示"用到 effective_limit 的多少比例时触发"，
        # 0.75 → request_tokens >= 75% effective_limit 时压缩。
        # transient = effective_limit × (1 - fraction) 推导自
        # auto_compact_at = effective_limit - transient = effective_limit × fraction。
        fraction = max(0.01, min(0.99, ModelConfig.summarization_trigger_fraction))
        transient = max(1, int(effective_limit * (1.0 - fraction)))

    prompt = (
        "Extract continuation context from this conversation. Preserve user goals, "
        "requirements, decisions and reasons, files, errors and fixes, rejected "
        "approaches, completed work, pending tasks and the next step. Return only "
        "the structured summary.\n\n{messages}"
    )

    def summarize(messages: list) -> str:
        return summary_model.invoke(prompt.format(messages=messages)).text.strip()

    async def async_summarize(messages: list) -> str:
        response = await summary_model.ainvoke(prompt.format(messages=messages))
        return response.text.strip()

    def request_tokens(request) -> int:
        messages = list(request.messages)
        if request.system_message is not None:
            messages.insert(0, request.system_message)
        return count_tokens_approximately(messages, tools=list(request.tools))

    return {
        "token_counter": count_tokens_approximately,
        "request_token_counter": request_tokens,
        "summarize": summarize,
        "async_summarize": async_summarize,
        "compaction_thresholds": CompactionThresholds(
            model_input_limit=model_limit,
            summary_output_reserve=reserve,
            transient_request_buffer=transient,
            final_request_guard=max(512, transient // 4),
        ),
        "compaction_keep_messages": ModelConfig.summarization_messages_to_keep,
    }


def build_noesis_middleware(
    *,
    profile: str,
    model: Any,
    model_id: str | None = None,
    tools: Sequence[BaseTool] = (),
    backend: BackendProtocol | None = None,
    dynamic_context_provider: DynamicContextProvider | None = None,
    workspace: str | None = None,
    session_id: str | None = None,
    attachments: Sequence[str] = (),
    skills: Sequence[str | tuple[str, str]] = (),
    skills_user_id: str | None = None,
    skills_system_prompt: str | None = None,
    memory: Sequence[str] = (),
    memory_system_prompt: str | None = None,
    todo: bool = False,
    subagents: Sequence[SubAgent | CompiledSubAgent] = (),
    async_subagents: Sequence[AsyncSubAgent] = (),
    snip: bool = False,
    middleware: Sequence[AgentMiddleware] = (),
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None,
    model_call_limit: int | None = None,
    tool_call_limit: int | None = None,
) -> list[AgentMiddleware]:
    if dynamic_context_provider is None:
        def dynamic_context_provider() -> DynamicContextBlock:
            now = datetime.now().astimezone()
            return DynamicContextBlock(
                current_time=now.isoformat(timespec="seconds"),
                timezone=str(now.tzinfo),
                workspace=workspace,
                session_id=session_id,
                attachments=tuple(sorted(set(attachments))),
            )
    return build_noesis_stack(
        NoesisStackDeps(
            profile=profile,
            tools=tools,
            backend=backend,
            dynamic_context_provider=dynamic_context_provider,
            skills_sources=skills,
            skills_user_id=skills_user_id,
            skills_system_prompt=skills_system_prompt,
            memory_sources=memory,
            memory_system_prompt=memory_system_prompt,
            todo=todo,
            subagents=subagents,
            async_subagents=async_subagents,
            enable_snip=snip,
            interrupt_on=interrupt_on if HitlConfig.enabled else None,
            model_call_limit=model_call_limit,
            tool_call_limit=tool_call_limit,
            llm_max_retries=int(ModelConfig.max_retries),
            tool_result_max_chars=int(getattr(ModelConfig, "tool_output_max_chars", 24_000)),
            middleware=middleware,
            **_compaction_deps(model, model_id),
        )
    )


def create_noesis_agent(
    *,
    system_prompt: str,
    checkpointer,
    profile: str,
    tools: Sequence[BaseTool] = (),
    backend: BackendProtocol | None = None,
    dynamic_context_provider: DynamicContextProvider | None = None,
    workspace: str | None = None,
    session_id: str | None = None,
    attachments: Sequence[str] = (),
    skills: Sequence[str | tuple[str, str]] = (),
    skills_user_id: str | None = None,
    skills_system_prompt: str | None = None,
    memory: Sequence[str] = (),
    memory_system_prompt: str | None = None,
    todo: bool = False,
    subagents: Sequence[SubAgent | CompiledSubAgent] = (),
    async_subagents: Sequence[AsyncSubAgent] = (),
    snip: bool = False,
    middleware: Sequence[AgentMiddleware] = (),
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None,
    model_call_limit: int | None = None,
    tool_call_limit: int | None = None,
    model=None,
    model_id: str | None = None,
    **create_agent_kwargs: Any,
):
    """Map direct DeepAgents-style arguments to one LangChain middleware stack."""
    resolved_model = model if model is not None else get_llm(model_id=model_id)
    stack = build_noesis_middleware(
        profile=profile,
        model=resolved_model,
        model_id=model_id,
        tools=tools,
        backend=backend,
        dynamic_context_provider=dynamic_context_provider,
        workspace=workspace,
        session_id=session_id,
        attachments=attachments,
        skills=skills,
        skills_user_id=skills_user_id,
        skills_system_prompt=skills_system_prompt,
        memory=memory,
        memory_system_prompt=memory_system_prompt,
        todo=todo,
        subagents=subagents,
        async_subagents=async_subagents,
        snip=snip,
        middleware=middleware,
        interrupt_on=interrupt_on,
        model_call_limit=model_call_limit,
        tool_call_limit=tool_call_limit,
    )
    return create_agent(
        model=resolved_model,
        tools=list(tools),
        system_prompt=system_prompt,
        checkpointer=checkpointer,
        middleware=stack,
        **create_agent_kwargs,
    )


__all__ = [
    "MiddlewareInventoryEntry",
    "build_noesis_middleware",
    "create_noesis_agent",
    "middleware_inventory",
]
