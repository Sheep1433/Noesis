"""Dynamic context source middleware.

Rebuilds the dynamic stable context (current time/timezone, workspace/session
identity, attachment manifest) at every model call and injects it as a stable,
cacheable block appended to the system prompt. This is the Noesis owner for
"dynamic context sources" described in the Claude Code context pipeline:
upstream has no equivalent component.

Design contract (``simplify-agent-context-architecture`` §6):

- injects current date/timezone, workspace/session identifier and attachment
  index at each model call;
- reads already-resolved run context — it does **not** access the database or
  any ``noesis.services`` module;
- produces a stable, cacheable block order, dynamic block after the static
  prompt prefix;
- re-runs naturally after compaction so dynamic sources are restored;
- does not scan Skills/Memory, does not compute token budget, does not modify
  conversation history, does not record usage.

Self-containment: the only runtime dependency is the injected
``context_provider`` (or, in its absence, a minimal fallback reading LangGraph
run metadata). No ``runtime``/``service`` calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
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

    from langgraph.config import RunnableConfig

# Reuse the upstream helper so the injected block follows the exact same
# content-block layout as DeepAgents Memory/Skills injection.
from deepagents.middleware._utils import append_to_system_message


DEFAULT_DYNAMIC_TEMPLATE = """## Runtime Context

Current time: {current_time} ({timezone})
{workspace_line}{session_line}{attachments_line}"""


@dataclass(frozen=True)
class DynamicContextBlock:
    """Immutable snapshot of the dynamic context for one model call.

    All fields are pre-formatted strings so the rendered block is byte-stable
    across re-runs as long as the inputs are equal.
    """

    current_time: str
    timezone: str
    workspace: str | None = None
    session_id: str | None = None
    attachments: tuple[str, ...] = field(default_factory=tuple)


DynamicContextProvider = Callable[[], "DynamicContextBlock | Awaitable[DynamicContextBlock]"]
"""Provider invoked at each model call to resolve the dynamic block.

Sync providers return a :class:`DynamicContextBlock`; async providers return
an awaitable. The factory injects a closure over the already-resolved run
context (workspace, session, attachment manifest) — this middleware never
reaches into the database or a service to obtain them.
"""


def render_dynamic_block(block: DynamicContextBlock, *, template: str = DEFAULT_DYNAMIC_TEMPLATE) -> str:
    """Render a dynamic context block into a stable text section.

    Empty optional fields collapse to empty lines (no stray headers) so the
    block stays cacheable when some sources are absent.
    """
    workspace_line = f"Workspace: {block.workspace}\n" if block.workspace else ""
    session_line = f"Session: {block.session_id}\n" if block.session_id else ""
    if block.attachments:
        joined = ", ".join(block.attachments)
        attachments_line = f"Attachments: {joined}\n"
    else:
        attachments_line = ""
    return template.format(
        current_time=block.current_time,
        timezone=block.timezone,
        workspace_line=workspace_line,
        session_line=session_line,
        attachments_line=attachments_line,
    ).rstrip()


def _thread_id_from_config(config: RunnableConfig | None) -> str | None:
    """Read ``thread_id`` from LangGraph run config without touching a service."""
    if not config:
        return None
    try:
        value = config.get("configurable", {}).get("thread_id")
    except AttributeError:
        return None
    return str(value) if value is not None else None


def _default_block(now: datetime | None = None, config: RunnableConfig | None = None) -> DynamicContextBlock:
    """Minimal fallback block: current UTC time + thread_id from run config.

    Used only when no provider is injected. The scene Agent is expected to
    inject a provider carrying the resolved workspace/attachment manifest.
    """
    moment = now if now is not None else datetime.now(timezone.utc)
    return DynamicContextBlock(
        current_time=moment.strftime("%Y-%m-%d %H:%M:%S"),
        timezone=str(moment.tzinfo or timezone.utc),
        session_id=_thread_id_from_config(config),
    )


class DynamicContextMiddleware(AgentMiddleware[AgentState[ResponseT], ContextT, ResponseT]):
    """Inject the dynamic stable context at every model call.

    The block is appended to the system message (after the static prompt and
    any earlier stable-source blocks) so it stays cacheable and re-runs
    naturally after compaction.
    """

    def __init__(
        self,
        context_provider: DynamicContextProvider | None = None,
        *,
        template: str = DEFAULT_DYNAMIC_TEMPLATE,
    ) -> None:
        self._context_provider = context_provider
        self._template = template

    # -- core injection -------------------------------------------------

    def modify_request(self, request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
        """Return a new request with the dynamic block appended to system."""
        block = self._resolve_block(request)
        text = render_dynamic_block(block, template=self._template)
        if not text:
            return request
        return request.override(
            system_message=append_to_system_message(request.system_message, text),
        )

    def _resolve_block(self, request: ModelRequest[ContextT]) -> DynamicContextBlock:
        if self._context_provider is None:
            config: RunnableConfig | None = None
            runtime = getattr(request, "runtime", None)
            if runtime is not None:
                config = getattr(runtime, "config", None)
            return _default_block(config=config)
        result = self._context_provider()
        # Async providers are only supported on the async path; a coroutine
        # returned here is a caller bug — surface it rather than silently dropping.
        if hasattr(result, "__await__"):
            close = getattr(result, "close", None)
            if callable(close):
                close()
            raise TypeError(
                "DynamicContextMiddleware: async context_provider passed to the sync "
                "wrap_model_call path; use awrap_model_call instead.",
            )
        return result  # type: ignore[return-value]

    async def _aresolve_block(self, request: ModelRequest[ContextT]) -> DynamicContextBlock:
        if self._context_provider is None:
            config: RunnableConfig | None = None
            runtime = getattr(request, "runtime", None)
            if runtime is not None:
                config = getattr(runtime, "config", None)
            return _default_block(config=config)
        result = self._context_provider()
        if hasattr(result, "__await__"):
            result = await result  # type: ignore[assignment]
        return result  # type: ignore[return-value]

    # -- AgentMiddleware seams ------------------------------------------

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        modified = self.modify_request(request)
        return handler(modified)

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        block = await self._aresolve_block(request)
        text = render_dynamic_block(block, template=self._template)
        if not text:
            modified = request
        else:
            modified = request.override(
                system_message=append_to_system_message(request.system_message, text),
            )
        return await handler(modified)


__all__ = [
    "DEFAULT_DYNAMIC_TEMPLATE",
    "DynamicContextBlock",
    "DynamicContextMiddleware",
    "DynamicContextProvider",
    "render_dynamic_block",
]
