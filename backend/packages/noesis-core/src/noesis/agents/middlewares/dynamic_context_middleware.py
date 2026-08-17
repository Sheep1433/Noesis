"""Inject a run-stable block of already-resolved dynamic context."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, NotRequired

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
from langgraph.runtime import Runtime

if TYPE_CHECKING:
    from collections.abc import Awaitable


DEFAULT_DYNAMIC_TEMPLATE = """## Runtime Context

Current time: {current_time} ({timezone})
{workspace_line}{session_line}{attachments_line}"""


@dataclass(frozen=True)
class DynamicContextBlock:
    """Resolved dynamic inputs for one agent run."""

    current_time: str
    timezone: str
    workspace: str | None = None
    session_id: str | None = None
    attachments: tuple[str, ...] = field(default_factory=tuple)


class DynamicContextState(AgentState):
    """Private rendered block fixed at the ``before_agent`` boundary."""

    dynamic_context_block: NotRequired[Annotated[str, PrivateStateAttr]]


# State keys this middleware owns; subagent isolation must carry these over.
PRIVATE_STATE_KEYS: tuple[str, ...] = ("dynamic_context_block",)


DynamicContextProvider = Callable[[], "DynamicContextBlock | Awaitable[DynamicContextBlock]"]


def render_dynamic_block(
    block: DynamicContextBlock,
    *,
    template: str = DEFAULT_DYNAMIC_TEMPLATE,
) -> str:
    """Render resolved inputs in a deterministic order."""
    workspace_line = f"Workspace: {block.workspace}\n" if block.workspace else ""
    session_line = f"Session: {block.session_id}\n" if block.session_id else ""
    attachments_line = (
        f"Attachments: {', '.join(block.attachments)}\n" if block.attachments else ""
    )
    return template.format(
        current_time=block.current_time,
        timezone=block.timezone,
        workspace_line=workspace_line,
        session_line=session_line,
        attachments_line=attachments_line,
    ).rstrip()


class DynamicContextMiddleware(
    AgentMiddleware[DynamicContextState, ContextT, ResponseT],
):
    """Resolve dynamic context once per run and inject that stable snapshot."""

    state_schema = DynamicContextState

    def __init__(
        self,
        context_provider: DynamicContextProvider | None = None,
        *,
        template: str = DEFAULT_DYNAMIC_TEMPLATE,
    ) -> None:
        self._context_provider = context_provider
        self._template = template

    def before_agent(
        self,
        state: DynamicContextState,  # noqa: ARG002
        runtime: Runtime[ContextT],  # noqa: ARG002
    ) -> dict[str, str] | None:
        """Resolve the block at the LangChain run boundary."""
        if self._context_provider is None:
            return {"dynamic_context_block": ""}
        block = self._context_provider()
        if inspect.isawaitable(block):
            close = getattr(block, "close", None)
            if callable(close):
                close()
            raise TypeError(
                "DynamicContextMiddleware: async context_provider requires an async agent invocation",
            )
        return {"dynamic_context_block": render_dynamic_block(block, template=self._template)}

    async def abefore_agent(
        self,
        state: DynamicContextState,  # noqa: ARG002
        runtime: Runtime[ContextT],  # noqa: ARG002
    ) -> dict[str, str] | None:
        """Resolve sync or async providers once at the async run boundary."""
        if self._context_provider is None:
            return {"dynamic_context_block": ""}
        block = self._context_provider()
        if inspect.isawaitable(block):
            block = await block
        return {"dynamic_context_block": render_dynamic_block(block, template=self._template)}

    @staticmethod
    def _with_context(request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
        text = request.state.get("dynamic_context_block")
        if not text:
            return request
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
    "DEFAULT_DYNAMIC_TEMPLATE",
    "DynamicContextBlock",
    "DynamicContextMiddleware",
    "DynamicContextProvider",
    "DynamicContextState",
    "PRIVATE_STATE_KEYS",
    "render_dynamic_block",
]
