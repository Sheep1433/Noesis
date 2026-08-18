"""Inject bounded durable references kept outside conversation history."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, NotRequired, TypedDict

from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
    ResponseT,
)
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.runtime import Runtime

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Mapping


MAX_DURABLE_REFS = 24
MAX_DURABLE_REF_CHARS = 256
MAX_COMPACT_INSTRUCTION_CHARS = 1_000

DURABLE_TEMPLATE = """## Durable Context

active_plan: {active_plan}
pending_tasks: {pending_tasks}
delegation_ledger: {delegation_ledger}
loaded_skills: {loaded_skills}
active_files: {active_files}
discovered_tools: {discovered_tools}{compact_instructions_line}"""


class DurableContext(TypedDict, total=False):
    """Serializable references that must survive conversation compaction."""

    active_plan_ref: str | None
    pending_tasks: list[str]
    delegation_ledger: list[str]
    loaded_skill_refs: list[str]
    active_file_refs: list[str]
    discovered_tool_refs: list[str]
    user_compact_instructions: str | None


class DurableContextState(AgentState):
    """LangChain state schema; durable context is private input/output state."""

    durable_context: NotRequired[Annotated[DurableContext, PrivateStateAttr]]


# State keys this middleware owns; subagent isolation must carry these over.
PRIVATE_STATE_KEYS: tuple[str, ...] = ("durable_context",)


def _bounded_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def _bounded_refs(values: object) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    refs: list[str] = []
    seen: set[str] = set()
    for value in values:
        ref = _bounded_text(value, MAX_DURABLE_REF_CHARS)
        if ref is None or ref in seen:
            continue
        seen.add(ref)
        refs.append(ref)
        if len(refs) == MAX_DURABLE_REFS:
            break
    return refs


def normalize_durable_context(raw: Mapping[str, object] | None) -> DurableContext:
    """Return a serializable, deduplicated and bounded durable snapshot."""
    if not raw:
        return {}
    normalized: DurableContext = {
        "pending_tasks": _bounded_refs(raw.get("pending_tasks")),
        "delegation_ledger": _bounded_refs(raw.get("delegation_ledger")),
        "loaded_skill_refs": _bounded_refs(raw.get("loaded_skill_refs")),
        "active_file_refs": _bounded_refs(raw.get("active_file_refs")),
        "discovered_tool_refs": _bounded_refs(raw.get("discovered_tool_refs")),
    }
    active_plan = _bounded_text(raw.get("active_plan_ref"), MAX_DURABLE_REF_CHARS)
    instructions = _bounded_text(
        raw.get("user_compact_instructions"),
        MAX_COMPACT_INSTRUCTION_CHARS,
    )
    if active_plan is not None:
        normalized["active_plan_ref"] = active_plan
    if instructions is not None:
        normalized["user_compact_instructions"] = instructions
    return normalized


def _has_content(context: DurableContext) -> bool:
    return any(
        (
            context.get("active_plan_ref"),
            context.get("pending_tasks"),
            context.get("delegation_ledger"),
            context.get("loaded_skill_refs"),
            context.get("active_file_refs"),
            context.get("discovered_tool_refs"),
            context.get("user_compact_instructions"),
        ),
    )


def _compaction_applied(state: DurableContextState) -> bool:
    """本轮会话是否已触发过压缩（CompactionMiddleware 写入过 event）。

    Durable context 是压缩后的关键状态兜底通道：未压缩时对话历史本身
    携带 todos / 文件记录 / 委派信息，注入便签纯属重复，且每步变化会
    打断 system prompt 前缀缓存。仅在压缩发生后才需要注入。
    """
    policy = state.get("compaction")
    return isinstance(policy, dict) and isinstance(policy.get("event"), dict)


def _join_refs(values: Iterable[str] | None) -> str:
    return ", ".join(values or ()) or "(none)"


def render_durable_block(
    context: DurableContext,
    *,
    template: str = DURABLE_TEMPLATE,
) -> str:
    """Render a normalized durable snapshot."""
    instructions = context.get("user_compact_instructions")
    instructions_line = f"\ncompact_instructions: {instructions}" if instructions else ""
    return template.format(
        active_plan=context.get("active_plan_ref") or "(none)",
        pending_tasks=_join_refs(context.get("pending_tasks")),
        delegation_ledger=_join_refs(context.get("delegation_ledger")),
        loaded_skills=_join_refs(context.get("loaded_skill_refs")),
        active_files=_join_refs(context.get("active_file_refs")),
        discovered_tools=_join_refs(context.get("discovered_tool_refs")),
        compact_instructions_line=instructions_line,
    ).rstrip()


def _label(value: object, *keys: str) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
    for key in keys:
        candidate = getattr(value, key, None)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def derive_durable_context(state: DurableContextState) -> DurableContext:
    """Capture compact-safe references from their real state owners."""
    current = normalize_durable_context(state.get("durable_context"))
    derived: dict[str, object] = dict(current)

    todos = state.get("todos")
    if isinstance(todos, list):
        derived["pending_tasks"] = [
            label
            for todo in todos
            if not (isinstance(todo, dict) and todo.get("status") == "completed")
            if (label := _label(todo, "content", "task", "description"))
        ]

    skills = state.get("skills_metadata")
    if isinstance(skills, list):
        derived["loaded_skill_refs"] = [
            label
            for skill in skills
            if (label := _label(skill, "path", "name", "skill_name"))
        ]

    read_versions = state.get("_read_before_write_versions")
    if isinstance(read_versions, dict):
        derived["active_file_refs"] = [str(path) for path in read_versions]

    active_files: list[str] = []
    delegations: list[str] = []
    for message in state.get("messages", []):
        if isinstance(message, ToolMessage):
            metadata = message.additional_kwargs
            read_mark = metadata.get("noesis_read_mark") or metadata.get("read_mark")
            path = _label(read_mark, "path")
            if path:
                active_files.append(path)
            if message.name == "task":
                delegations.append(f"{message.tool_call_id}:completed")
        elif isinstance(message, AIMessage):
            for call in message.tool_calls:
                if call.get("name") == "task" and call.get("id"):
                    delegations.append(f"{call['id']}:started")
    if active_files:
        derived["active_file_refs"] = active_files
    if delegations:
        derived["delegation_ledger"] = delegations
    return normalize_durable_context(derived)


class DurableContextMiddleware(
    AgentMiddleware[DurableContextState, ContextT, ResponseT],
):
    """Normalize once per run and inject durable private state at model calls."""

    state_schema = DurableContextState

    def __init__(self, *, template: str = DURABLE_TEMPLATE) -> None:
        self._template = template

    def before_agent(
        self,
        state: DurableContextState,
        runtime: Runtime[ContextT],  # noqa: ARG002
    ) -> dict[str, DurableContext] | None:
        raw = state.get("durable_context")
        if raw is None:
            return None
        normalized = normalize_durable_context(raw)
        if normalized == raw:
            return None
        return {"durable_context": normalized}

    def before_model(
        self,
        state: DurableContextState,
        runtime: Runtime[ContextT],  # noqa: ARG002
    ) -> dict[str, DurableContext] | None:
        if not _compaction_applied(state):
            return None
        derived = derive_durable_context(state)
        if derived == normalize_durable_context(state.get("durable_context")):
            return None
        return {"durable_context": derived}

    async def abefore_model(
        self,
        state: DurableContextState,
        runtime: Runtime[ContextT],
    ) -> dict[str, DurableContext] | None:
        return self.before_model(state, runtime)

    @staticmethod
    def _context(request: ModelRequest[ContextT]) -> DurableContext:
        return normalize_durable_context(request.state.get("durable_context"))

    def _with_context(self, request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
        if not _compaction_applied(request.state):  # type: ignore[arg-type]
            return request
        context = self._context(request)
        if not _has_content(context):
            return request
        text = render_durable_block(context, template=self._template)
        return request.override(
            system_message=append_to_system_message(request.system_message, text),
        )

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        return handler(self._with_context(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        return await handler(self._with_context(request))


__all__ = [
    "DURABLE_TEMPLATE",
    "MAX_COMPACT_INSTRUCTION_CHARS",
    "MAX_DURABLE_REFS",
    "MAX_DURABLE_REF_CHARS",
    "DurableContext",
    "DurableContextMiddleware",
    "DurableContextState",
    "PRIVATE_STATE_KEYS",
    "derive_durable_context",
    "normalize_durable_context",
    "render_durable_block",
]
