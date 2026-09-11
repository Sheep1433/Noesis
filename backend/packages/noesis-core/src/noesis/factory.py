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
from langchain_core.messages import HumanMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.tools import BaseTool

from noesis.agents.middlewares import (
    CompactionMiddleware,
    CompactionThresholds,
    DynamicContextBlock,
    DynamicContextProvider,
)
from noesis.agents.middlewares.compaction_middleware import COMPACTION_SUMMARY_TAG
from noesis.agents.middlewares.stack import NoesisStackDeps, build_noesis_stack
from noesis.config.env import HitlConfig, ModelConfig
from noesis.llm.factory import get_llm
from noesis.llm.model_limits import resolve_context_max_tokens
from noesis.services.history_search import record_compaction_boundary
from noesis.storage.postgres.manager import pg_manager

# 压缩摘要指令：作为最后一条 HumanMessage 送达（尾部 = 模型注意力所在），
# 结构化八节 checkpoint 给模型可执行的锚。基调对齐 codex compact 的交接
# 定位：摘要不承载一切——被压缩区用户消息原文由投影单独装回，摘要只
# 负责决策、状态与结论，因此要求简洁而非尽预算详尽。逐节保留、空节
# "(none)"、prior checkpoint 合并不逐字复制、禁止元话语与工具调用。
COMPACTION_SUMMARY_INSTRUCTION = "\n".join([
    "You are performing a CONTEXT CHECKPOINT COMPACTION. Condense the "
    "conversation ABOVE into a handoff summary that lets another model resume "
    "the work with no access to the original conversation.",
    "",
    "Output EXACTLY the Markdown structure below: keep every section, in order. "
    "Use dense bullet points, not prose paragraphs. "
    'Write "(none)" for an empty section — never drop a section.',
    "",
    "## Primary Request and Intent",
    "- [the user's original and evolving goals; quote verbatim where the exact wording matters]",
    "",
    "## Key Technical Concepts",
    "- [technologies, frameworks, patterns, and conventions in play]",
    "",
    "## Files and Code",
    "- [exact path: why it matters, key changes or snippets]",
    "",
    "## Errors and Fixes",
    "- [error: how it was resolved, plus any related user feedback]",
    "",
    "## Pending Jobs",
    "- [explicitly requested work not yet completed]",
    "",
    "## Current Work",
    "- [precisely what was in progress at this checkpoint]",
    "",
    "## Next Step",
    '- [the single next action, directly in line with the most recent request, or "(none)"]',
    "",
    "## Critical Context",
    "- [decisions and their rationale, constraints, user preferences, rejected "
    "approaches and why, open questions, data needed to continue]",
    "",
    "Rules:",
    "- Write in the conversation's language. Be concise and structured: each "
    "bullet carries exactly ONE atomic fact — a single explanation, "
    "conclusion, or decision with its rationale; never merge multiple facts "
    "into narrative sentences.",
    "- If the conversation spans multiple topics or phases over time, give "
    "every phase at least its goal and outcome — do not omit earlier phases "
    "because they are old.",
    "- Prioritize verbatim detail in 'Files and Code' and 'Errors and Fixes': "
    "exact commands, full error strings, config values, line numbers, function "
    "signatures, and identifiers — quote verbatim rather than paraphrase.",
    "- Capture user feedback and explicit instructions faithfully, especially corrections.",
    "- Do NOT mention this summarization request or that the context was compacted.",
    "- Output only the checkpoint text: do not call any tool or take any other action.",
    "- If the conversation already contains a previous checkpoint, it is a PRIOR "
    "summary. Do not copy it forward verbatim: preserve still-true facts, drop "
    "stale ones, and merge newer information into a single consolidated summary "
    "under the same structure.",
])


def _annotate_builtin_tools(tools: Sequence[BaseTool]) -> None:
    for tool in tools:
        metadata = getattr(tool, "metadata", None)
        if not isinstance(metadata, dict):
            metadata = {}
            tool.metadata = metadata
        metadata.setdefault("noesis_provider_key", "builtin")


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

    # 摘要输出不设上限（对齐 codex compact：summarize 即普通模型回合）；
    # 质量防线在中间件的摘要校验 + 重试，不在请求层封顶
    summary_model = get_llm(purpose="summarization", model_id=model_id)

    def _summary_request(messages: list) -> list:
        # 指令必须作为最后一条 HumanMessage：消息序列原样重放，指令放在
        # 模型注意力所在的尾部。放在头部会被超长内容垫出注意力区——
        # 745K 输入实证头部指令全盲、模型转而从尾部续写会话。
        return [*messages, HumanMessage(content=COMPACTION_SUMMARY_INSTRUCTION)]

    def summarize(messages: list) -> str:
        return summary_model.invoke(
            _summary_request(messages),
            config={"tags": [COMPACTION_SUMMARY_TAG]},
        ).text.strip()

    async def async_summarize(messages: list) -> str:
        response = await summary_model.ainvoke(
            _summary_request(messages),
            config={"tags": [COMPACTION_SUMMARY_TAG]},
        )
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
        "compaction_user_message_tokens": ModelConfig.summarization_user_message_tokens,
    }


def _compaction_boundary_writer(session_id: str):
    """压缩成功 → 写 t_chat_session.compaction_cutoff_seq（session-history-search）。

    每次调用独立开短事务连接；失败由中间件侧记日志降级（边界缺失 =
    before_compaction 检索退化为全历史），不阻断压缩主流程。
    """
    async def _write_boundary(thread_id: str) -> None:
        async with pg_manager.get_async_session_context() as db:
            await record_compaction_boundary(db, session_id=session_id)

    return _write_boundary


def build_compaction_middleware(
    *,
    model_id: str | None = None,
    session_id: str | None = None,
) -> CompactionMiddleware | None:
    """Build the shared compaction engine for host/runtime operations.

    Normal Agent calls receive the same dependencies through
    ``build_noesis_middleware``. The host-level ``/compact`` command uses this
    seam to update a checkpoint without creating a model turn. ``session_id``
    additionally wires the compaction boundary write for history search.
    """
    deps = _compaction_deps(model=None, model_id=model_id)
    if not deps:
        return None
    # deps 的键名对齐 NoesisStackDeps（compaction_thresholds /
    # compaction_keep_messages / compaction_user_message_tokens）；
    # 本 seam 直构中间件，须映射回构造参数名
    # （此前 **deps 直传会 TypeError，/compact 宿主路径从未真正跑通）
    return CompactionMiddleware(
        boundary_writer=_compaction_boundary_writer(session_id) if session_id else None,
        token_counter=deps["token_counter"],
        request_token_counter=deps["request_token_counter"],
        summarize=deps["summarize"],
        async_summarize=deps["async_summarize"],
        thresholds=deps["compaction_thresholds"],
        keep_messages=deps["compaction_keep_messages"],
        user_message_budget_tokens=deps["compaction_user_message_tokens"],
    )


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
    filesystem_middleware_hook: Any = None,
    compaction_enabled: bool = True,
) -> list[AgentMiddleware]:
    """``compaction_enabled=False`` 时不装配 CompactionMiddleware（栈内
    summarize/thresholds 缺席即跳过）：压缩评测的不压缩组（上限参照）用它
    保持完整原文历史不被压缩。"""
    if dynamic_context_provider is None:
        def dynamic_context_provider() -> DynamicContextBlock:
            now = datetime.now().astimezone()
            return DynamicContextBlock(
                # 日期粒度（Claude Code 同款）：头部冻结块一天内逐字节不变，
                # 跨日由中间件尾部纠正声明处理
                date=now.date().isoformat(),
                timezone=str(now.tzinfo),
                workspace=workspace,
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
            read_file_max_chars=int(getattr(ModelConfig, "read_file_max_chars", 20_000)),
            middleware=middleware,
            session_id=session_id or "",
            filesystem_middleware_hook=filesystem_middleware_hook,
            # 压缩边界写仅主 loop 上的 profile：SUBAGENT worker 跑在隔离
            # loop，pg_manager 连接池绑定主 loop，跨 loop 直连会报错
            compaction_boundary_writer=(
                _compaction_boundary_writer(session_id)
                if session_id and profile != "SUBAGENT"
                else None
            ),
            **(_compaction_deps(model, model_id) if compaction_enabled else {}),
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
    filesystem_middleware_hook: Any = None,
    compaction_enabled: bool = True,
    model=None,
    model_id: str | None = None,
    **create_agent_kwargs: Any,
):
    """Map direct DeepAgents-style arguments to one LangChain middleware stack."""
    _annotate_builtin_tools(tools)
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
        filesystem_middleware_hook=filesystem_middleware_hook,
        compaction_enabled=compaction_enabled,
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
    "build_compaction_middleware",
    "create_noesis_agent",
    "middleware_inventory",
]
