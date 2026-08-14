"""Unit contracts for deferred schema filtering and execution guards."""

from __future__ import annotations

from typing import Any

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.prebuilt.tool_node import ToolCallRequest
from pydantic import BaseModel

from noesis.agents.middlewares.deferred_tool_filter_middleware import DeferredToolFilterMiddleware
from noesis.agents.runtime.tool_registry import ToolRegistry


class _Args(BaseModel):
    x: int = 0


def _small_tool(name: str = "read_file") -> StructuredTool:
    return StructuredTool.from_function(
        name=name,
        func=lambda x=0: "ok",
        description="small",
        args_schema=_Args,
    )


def _big_tool(name: str) -> StructuredTool:
    return StructuredTool.from_function(
        name=name,
        func=lambda x=0: "ok",
        description="B" * 5_000,
        args_schema=_Args,
    )


def _request(tools: list[Any], state: dict[str, Any] | None = None) -> ModelRequest:
    return ModelRequest(
        model=object(),  # type: ignore[arg-type]
        messages=[],
        system_message=SystemMessage(content="sys"),
        tools=tools,
        state=state if state is not None else {"messages": []},
    )


def _tool_request(name: str, state: dict[str, Any], tool=None) -> ToolCallRequest:  # noqa: ANN001
    return ToolCallRequest(
        tool_call={"name": name, "args": {}, "id": "call-1"},
        tool=tool,
        state=state,
        runtime=None,
    )


def _promote(registry: ToolRegistry, query: str, state: dict[str, Any]) -> None:
    result = registry.tool_search.func(  # type: ignore[union-attr]
        query=query,
        state=state,
        tool_call_id="search-1",
    )
    state.update(result.update)


def test_middleware_only_exposes_registry_visible_tools() -> None:
    registry = ToolRegistry(
        [_small_tool("read_file"), _big_tool("mcp_huge_tool")],
        large_schema_cost=200,
    )
    middleware = DeferredToolFilterMiddleware(registry=registry)

    modified = middleware.modify_request(_request([]))

    assert [tool.name for tool in modified.tools] == ["read_file", "tool_search"]
    assert registry.get("tool_search") is modified.tools[-1]
    assert middleware.tools == (registry.tool_search,)


def test_promoted_schema_becomes_visible_for_matching_catalog_hash() -> None:
    registry = ToolRegistry([_big_tool("mcp_search_engine")], large_schema_cost=200)
    middleware = DeferredToolFilterMiddleware(registry=registry)
    state: dict[str, Any] = {"messages": []}

    _promote(registry, "search", state)
    modified = middleware.modify_request(_request([], state))

    assert "mcp_search_engine" in [tool.name for tool in modified.tools]


def test_catalog_change_does_not_reuse_old_promotions() -> None:
    tools = [_big_tool("mcp_search_engine")]
    registry = ToolRegistry(catalog_provider=lambda: tools, large_schema_cost=200)
    middleware = DeferredToolFilterMiddleware(registry=registry)
    state: dict[str, Any] = {"messages": []}
    _promote(registry, "search", state)

    tools.append(_big_tool("mcp_calendar"))
    modified = middleware.modify_request(_request([], state))

    assert "mcp_search_engine" not in [tool.name for tool in modified.tools]
    assert len(state["_tool_catalog_discovered"]) == 1


def test_new_top_level_run_clears_previous_promotions() -> None:
    registry = ToolRegistry([_big_tool("mcp_search_engine")], large_schema_cost=200)
    middleware = DeferredToolFilterMiddleware(registry=registry)
    state: dict[str, Any] = {"messages": []}
    _promote(registry, "search", state)

    update = middleware.before_agent(state, None)  # type: ignore[arg-type]

    assert update == {"_tool_catalog_discovered": {}}


def test_sync_tool_hook_blocks_unpromoted_execution() -> None:
    tool = _big_tool("mcp_huge_tool")
    middleware = DeferredToolFilterMiddleware(
        registry=ToolRegistry([tool], large_schema_cost=200)
    )

    with pytest.raises(PermissionError, match="not been promoted"):
        middleware.wrap_tool_call(
            _tool_request(tool.name, {"messages": []}, tool),
            lambda request: ToolMessage(content="ran", tool_call_id="call-1"),
        )


@pytest.mark.asyncio
async def test_async_tool_hook_blocks_unpromoted_execution() -> None:
    tool = _big_tool("mcp_huge_tool")
    middleware = DeferredToolFilterMiddleware(
        registry=ToolRegistry([tool], large_schema_cost=200)
    )

    async def handler(request):  # noqa: ANN001
        return ToolMessage(content="ran", tool_call_id="call-1")

    with pytest.raises(PermissionError, match="not been promoted"):
        await middleware.awrap_tool_call(
            _tool_request(tool.name, {"messages": []}, tool),
            handler,
        )


def test_permission_is_rechecked_at_execution() -> None:
    allowed = False
    tool = _small_tool("restricted")
    registry = ToolRegistry(
        [tool],
        permissions={"restricted": lambda state: allowed},
        large_schema_cost=10_000,
    )
    middleware = DeferredToolFilterMiddleware(registry=registry)

    with pytest.raises(PermissionError, match="not permitted"):
        middleware.wrap_tool_call(
            _tool_request(tool.name, {"messages": []}, tool),
            lambda request: ToolMessage(content="ran", tool_call_id="call-1"),
        )


def test_sync_model_hook_passes_filtered_request_to_handler() -> None:
    registry = ToolRegistry([_big_tool("mcp_huge_tool")], large_schema_cost=200)
    middleware = DeferredToolFilterMiddleware(registry=registry)

    names = middleware.wrap_model_call(
        _request([]),
        lambda request: [tool.name for tool in request.tools],  # type: ignore[arg-type,return-value]
    )

    assert names == ["tool_search"]


@pytest.mark.asyncio
async def test_async_model_hook_passes_filtered_request_to_handler() -> None:
    registry = ToolRegistry([_big_tool("mcp_huge_tool")], large_schema_cost=200)
    middleware = DeferredToolFilterMiddleware(registry=registry)

    async def handler(request):  # noqa: ANN001
        return [tool.name for tool in request.tools]

    names = await middleware.awrap_model_call(_request([]), handler)  # type: ignore[arg-type]

    assert names == ["tool_search"]
