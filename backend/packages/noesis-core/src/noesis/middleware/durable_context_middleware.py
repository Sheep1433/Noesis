"""Durable context middleware — compaction-safe durable references.

Maintains the minimal durable context that must survive conversation
compaction: active plan ref, pending tasks, the delegation ledger, loaded
skill refs, active file refs, discovered tool refs, and user compact
instructions. This is the Noesis owner for the Claude Code "durable context"
step; upstream's todo tool lives *inside* conversation history and gets
summarised away during compaction.

Design contract (``simplify-agent-context-architecture`` §7):

- persist plan/task/skill/file/tool references and compact instructions as
  private state (``_durable_context``); do NOT copy full tool results, skill
  bodies or file bodies;
- update the delegation ledger after tool/subagent completion;
- inject a bounded durable-status block at each model call, after the dynamic
  block;
- compaction can only summarise conversation — it cannot delete durable
  context;
- sub-agents do not inherit this state by default; fork mode copies a
  whitelist subset.

Self-containment: pure private-state bookkeeping; no ``runtime``/``service``
calls. Updates are driven by explicit ``record_*`` methods (called from the
tool-call seam when delegation/file/tool events are observed) so the middleware
has no dependency on other middleware's state shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain_core.messages import SystemMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable

from deepagents.middleware._utils import append_to_system_message


DURABLE_TEMPLATE = """## Durable Context

active_plan: {active_plan}
pending_tasks: {pending_tasks}
delegation_ledger: {delegation_ledger}
loaded_skills: {loaded_skills}
active_files: {active_files}
discovered_tools: {discovered_tools}{compact_instructions_line}"""


@dataclass
class DurableContext:
    """Mutable durable-context ledger (serialised to private state each turn)."""

    active_plan_ref: str | None = None
    pending_tasks: list[str] = field(default_factory=list)
    delegation_ledger: list[str] = field(default_factory=list)
    loaded_skill_refs: list[str] = field(default_factory=list)
    active_file_refs: list[str] = field(default_factory=list)
    discovered_tool_refs: list[str] = field(default_factory=list)
    user_compact_instructions: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_plan_ref": self.active_plan_ref,
            "pending_tasks": list(self.pending_tasks),
            "delegation_ledger": list(self.delegation_ledger),
            "loaded_skill_refs": list(self.loaded_skill_refs),
            "active_file_refs": list(self.active_file_refs),
            "discovered_tool_refs": list(self.discovered_tool_refs),
            "user_compact_instructions": self.user_compact_instructions,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "DurableContext":
        if not data:
            return cls()
        return cls(
            active_plan_ref=data.get("active_plan_ref"),
            pending_tasks=list(data.get("pending_tasks") or []),
            delegation_ledger=list(data.get("delegation_ledger") or []),
            loaded_skill_refs=list(data.get("loaded_skill_refs") or []),
            active_file_refs=list(data.get("active_file_refs") or []),
            discovered_tool_refs=list(data.get("discovered_tool_refs") or []),
            user_compact_instructions=data.get("user_compact_instructions"),
        )


def render_durable_block(ctx: DurableContext, *, template: str = DURABLE_TEMPLATE) -> str:
    instructions_line = (
        f"\ncompact_instructions: {ctx.user_compact_instructions}"
        if ctx.user_compact_instructions
        else ""
    )
    return template.format(
        active_plan=ctx.active_plan_ref or "(none)",
        pending_tasks=", ".join(ctx.pending_tasks) or "(none)",
        delegation_ledger=", ".join(ctx.delegation_ledger) or "(none)",
        loaded_skills=", ".join(ctx.loaded_skill_refs) or "(none)",
        active_files=", ".join(ctx.active_file_refs) or "(none)",
        discovered_tools=", ".join(ctx.discovered_tool_refs) or "(none)",
        compact_instructions_line=instructions_line,
    ).rstrip()


class DurableContextMiddleware(AgentMiddleware[AgentState[ResponseT], ContextT, ResponseT]):
    """Maintain and inject the compaction-safe durable context ledger."""

    def __init__(self, *, template: str = DURABLE_TEMPLATE) -> None:
        self._template = template

    @staticmethod
    def _ctx(state: AgentState[Any]) -> DurableContext:
        return DurableContext.from_dict(state.get("_durable_context"))

    @staticmethod
    def _commit(state: AgentState[Any], ctx: DurableContext) -> None:
        state["_durable_context"] = ctx.to_dict()  # type: ignore[assignment]

    # -- explicit update API (driven by tool-call observation) ------------

    def set_active_plan(self, state: AgentState[Any], plan_ref: str | None) -> None:
        ctx = self._ctx(state)
        ctx.active_plan_ref = plan_ref
        self._commit(state, ctx)

    def add_task(self, state: AgentState[Any], task: str) -> None:
        ctx = self._ctx(state)
        if task and task not in ctx.pending_tasks:
            ctx.pending_tasks.append(task)
        self._commit(state, ctx)

    def complete_task(self, state: AgentState[Any], task: str) -> None:
        ctx = self._ctx(state)
        if task in ctx.pending_tasks:
            ctx.pending_tasks.remove(task)
        self._commit(state, ctx)

    def record_delegation(self, state: AgentState[Any], subagent: str, result_ref: str) -> None:
        ctx = self._ctx(state)
        ctx.delegation_ledger.append(f"{subagent}→{result_ref}")
        self._commit(state, ctx)

    def set_compact_instructions(self, state: AgentState[Any], instructions: str | None) -> None:
        ctx = self._ctx(state)
        ctx.user_compact_instructions = instructions
        self._commit(state, ctx)

    def merge_refs(
        self,
        state: AgentState[Any],
        *,
        skills: list[str] | None = None,
        files: list[str] | None = None,
        tools: list[str] | None = None,
    ) -> None:
        ctx = self._ctx(state)
        if skills is not None:
            ctx.loaded_skill_refs = list(dict.fromkeys([*ctx.loaded_skill_refs, *skills]))
        if files is not None:
            ctx.active_file_refs = list(dict.fromkeys([*ctx.active_file_refs, *files]))
        if tools is not None:
            ctx.discovered_tool_refs = list(dict.fromkeys([*ctx.discovered_tool_refs, *tools]))
        self._commit(state, ctx)

    def snapshot(self, state: AgentState[Any], *, whitelist: tuple[str, ...] | None = None) -> DurableContext:
        """Return a (optionally whitelisted) copy for fork-mode sub-agent inheritance."""
        ctx = self._ctx(state)
        if whitelist is None:
            return DurableContext(**ctx.to_dict())
        allowed = {
            "active_plan_ref": ctx.active_plan_ref,
            "pending_tasks": ctx.pending_tasks,
            "delegation_ledger": ctx.delegation_ledger,
            "loaded_skill_refs": ctx.loaded_skill_refs,
            "active_file_refs": ctx.active_file_refs,
            "discovered_tool_refs": ctx.discovered_tool_refs,
            "user_compact_instructions": ctx.user_compact_instructions,
        }
        return DurableContext(**{k: v for k, v in allowed.items() if k in whitelist})

    # -- model-call seam ------------------------------------------------

    def modify_request(self, request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
        ctx = self._ctx(request.state)
        # Only inject when there is something non-empty to say, so empty
        # durable state does not add noise to the system prompt.
        if not any(
            [
                ctx.active_plan_ref,
                ctx.pending_tasks,
                ctx.delegation_ledger,
                ctx.loaded_skill_refs,
                ctx.active_file_refs,
                ctx.discovered_tool_refs,
                ctx.user_compact_instructions,
            ]
        ):
            return request
        text = render_durable_block(ctx, template=self._template)
        return request.override(system_message=append_to_system_message(request.system_message, text))

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        return handler(self.modify_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        return await handler(self.modify_request(request))


__all__ = [
    "DURABLE_TEMPLATE",
    "DurableContext",
    "DurableContextMiddleware",
    "render_durable_block",
]
