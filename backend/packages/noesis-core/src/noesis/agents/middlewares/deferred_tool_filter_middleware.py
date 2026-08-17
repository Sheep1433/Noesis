"""Filter deferred tool schemas and guard deferred tool execution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Callable, NotRequired

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
    ResponseT,
)
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from noesis.agents.runtime.tool_registry import (
    ToolCatalogEntry,
    ToolRegistry,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from langchain_core.messages import ToolMessage


class DeferredToolState(AgentState):
    """Checkpointed promotions partitioned by authoritative catalog hash."""

    _tool_catalog_discovered: NotRequired[
        Annotated[dict[str, list[str]], PrivateStateAttr]
    ]


# State keys this middleware owns; subagent isolation must carry these over.
PRIVATE_STATE_KEYS: tuple[str, ...] = ("_tool_catalog_discovered",)


class DeferredToolFilterMiddleware(AgentMiddleware[DeferredToolState, ContextT, ResponseT]):
    """Expose only schemas promoted for the current catalog revision."""

    state_schema = DeferredToolState

    def __init__(
        self,
        *,
        registry: ToolRegistry | None = None,
        schema_budget_tokens: int = 8_000,
        large_schema_cost: int = 200,
        basic_predicate: Callable[[str], bool] | None = None,
        catalog_provider: Callable[[], list[Any]] | None = None,
    ) -> None:
        if registry is not None and catalog_provider is not None:
            raise ValueError("pass either registry or catalog_provider, not both")
        self.registry = registry or ToolRegistry(
            catalog_provider=catalog_provider,
            schema_budget_tokens=schema_budget_tokens,
            large_schema_cost=large_schema_cost,
            basic_predicate=basic_predicate,
        )
        self.tools = (self.registry.tool_search,)

    def modify_request(self, request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
        self.registry.refresh(request.tools or [])
        selected = self.registry.visible_tools(request.state)
        if list(request.tools or []) == selected:
            return request
        return request.override(tools=selected)

    def before_agent(
        self,
        state: DeferredToolState,  # noqa: ARG002
        runtime: Runtime[ContextT],  # noqa: ARG002
    ) -> dict[str, dict[str, list[str]]]:
        """Start each top-level invocation with no deferred promotions."""
        return {"_tool_catalog_discovered": {}}

    async def abefore_agent(
        self,
        state: DeferredToolState,
        runtime: Runtime[ContextT],
    ) -> dict[str, dict[str, list[str]]]:
        return self.before_agent(state, runtime)

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

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:  # type: ignore[override]
        self._authorize(request)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:  # type: ignore[override]
        self._authorize(request)
        return await handler(request)

    def _authorize(self, request: ToolCallRequest) -> None:
        name = str(request.tool_call.get("name") or "")
        self.registry.assert_executable(name, request.state)


__all__ = [
    "DeferredToolFilterMiddleware",
    "DeferredToolState",
    "PRIVATE_STATE_KEYS",
    "ToolCatalogEntry",
]
