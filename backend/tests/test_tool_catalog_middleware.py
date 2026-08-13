"""Unit contracts for ``ToolCatalogMiddleware`` (deferred schema + tool search)."""

from __future__ import annotations

from typing import Any

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import SystemMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from noesis.middleware.tool_catalog_middleware import ToolCatalogMiddleware


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
        func=lambda q="": "ok",
        description="B" * 5000,  # large description → high schema cost
        args_schema=_Args,
    )


def _request(tools, state=None) -> ModelRequest:
    return ModelRequest(
        model=object(),  # type: ignore[arg-type]
        messages=[],
        system_message=SystemMessage(content="sys"),
        tools=list(tools),
        state=state if state is not None else {"messages": []},
    )


def test_small_catalog_passes_through_unchanged() -> None:
    mw = ToolCatalogMiddleware(schema_budget_tokens=10000, large_schema_cost=200)
    tools = [_small_tool("read_file"), _small_tool("glob")]
    modified = mw.modify_request(_request(tools))
    assert [t.name for t in modified.tools] == [t.name for t in tools]


def test_large_deferred_tool_is_filtered_out() -> None:
    mw = ToolCatalogMiddleware(schema_budget_tokens=50, large_schema_cost=200)
    tools = [_small_tool("read_file"), _big_tool("mcp_huge_tool")]
    modified = mw.modify_request(_request(tools))
    names = [t.name for t in modified.tools]
    assert "read_file" in names  # basic tool kept
    assert "mcp_huge_tool" not in names  # deferred → filtered
    assert "tool_search" in names  # tool_search injected


def test_base_tools_always_bound_even_if_large() -> None:
    mw = ToolCatalogMiddleware(schema_budget_tokens=10, large_schema_cost=10)
    # read_file is "basic" per default predicate → always bound even if large
    big_basic = _big_tool("read_file")
    modified = mw.modify_request(_request([big_basic]))
    assert any(t.name == "read_file" for t in modified.tools)


def test_tool_search_activates_discovered_schema() -> None:
    catalog = [_big_tool("mcp_search_engine"), _big_tool("mcp_calendar")]
    mw = ToolCatalogMiddleware(catalog_provider=lambda: catalog, large_schema_cost=200)
    state = {"messages": []}

    # First model call: deferred tools filtered, tool_search present
    modified = mw.modify_request(_request([_small_tool("read_file")], state=state))
    assert "mcp_search_engine" not in [t.name for t in modified.tools]

    # Simulate the model calling tool_search("search")
    from langgraph.prebuilt.tool_node import ToolCallRequest
    from langchain_core.messages import ToolMessage

    tc_request = ToolCallRequest(
        tool_call={"name": "tool_search", "args": {"query": "search"}, "id": "ts1"},
        tool=None,
        state=state,
        runtime=None,
    )

    def handler(_req):  # noqa: ANN001
        return ToolMessage(content="activated", tool_call_id="ts1", name="tool_search")

    mw.wrap_tool_call(tc_request, handler)
    discovered = state.get("_tool_catalog_discovered", [])
    assert "mcp_search_engine" in discovered
    assert "mcp_calendar" not in discovered

    # Next model call: discovered tool now bound
    modified2 = mw.modify_request(_request([_small_tool("read_file")], state=state))
    names2 = [t.name for t in modified2.tools]
    assert "mcp_search_engine" in names2
    assert "mcp_calendar" not in names2


def test_no_deferred_tools_means_no_tool_search_injected() -> None:
    mw = ToolCatalogMiddleware(schema_budget_tokens=10000, large_schema_cost=200)
    modified = mw.modify_request(_request([_small_tool("read_file")]))
    assert "tool_search" not in [t.name for t in modified.tools]


def test_custom_basic_predicate() -> None:
    mw = ToolCatalogMiddleware(
        schema_budget_tokens=10,
        large_schema_cost=10,
        basic_predicate=lambda name: name == "my_core_tool",
    )
    modified = mw.modify_request(_request([_big_tool("my_core_tool"), _big_tool("other")]))
    names = [t.name for t in modified.tools]
    assert "my_core_tool" in names
    assert "other" not in names
