"""Contracts for the authoritative runtime tool registry."""

from __future__ import annotations

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from noesis.agents.runtime.tool_registry import ToolRegistry


class _Args(BaseModel):
    query: str = ""


def _tool(name: str, description: str = "small") -> StructuredTool:
    return StructuredTool.from_function(
        name=name,
        func=lambda query="": query,
        description=description,
        args_schema=_Args,
    )


def test_registry_owns_model_schema_revision_and_hash() -> None:
    tools = [_tool("mcp_search", "A" * 1_000)]
    registry = ToolRegistry(catalog_provider=lambda: tools, large_schema_cost=100)
    first_revision = registry.revision
    first_hash = registry.catalog_hash

    assert registry.schema_for("mcp_search") == {
        "name": "mcp_search",
        "description": "A" * 1_000,
        "args": tools[0].tool_call_schema.model_json_schema(),
    }

    tools[:] = [_tool("mcp_search", "changed" * 200)]
    registry.refresh()

    assert registry.revision == first_revision + 1
    assert registry.catalog_hash != first_hash


def test_tool_search_is_registered_with_sync_and_async_implementations() -> None:
    registry = ToolRegistry([_tool("mcp_search", "A" * 1_000)], large_schema_cost=100)
    state: dict = {}

    sync_result = registry.tool_search.func(  # type: ignore[union-attr]
        query="search",
        state=state,
        tool_call_id="call-1",
    )

    assert "mcp_search" in sync_result.update["messages"][0].content
    assert "mcp_search" in sync_result.update["_tool_catalog_discovered"][registry.catalog_hash]
    assert registry.get("tool_search") is registry.tool_search
    assert registry.tool_search.coroutine is not None  # type: ignore[attr-defined]
    properties = registry.tool_search.tool_call_schema.model_json_schema()["properties"]
    assert "state" not in properties
    assert "tool_call_id" not in properties


@pytest.mark.asyncio
async def test_async_tool_search_promotes_schema() -> None:
    registry = ToolRegistry([_tool("mcp_search", "A" * 1_000)], large_schema_cost=100)
    state: dict = {}

    result = await registry.tool_search.coroutine(  # type: ignore[attr-defined,misc]
        query="search",
        state=state,
        tool_call_id="call-1",
    )
    state.update(result.update)

    assert registry.promoted_names(state) == {"mcp_search"}


def test_registry_rejects_reserved_or_non_tool_entries() -> None:
    with pytest.raises(TypeError, match="BaseTool"):
        ToolRegistry([{"name": "not-real"}])  # type: ignore[list-item]

    with pytest.raises(ValueError, match="reserved"):
        ToolRegistry([_tool("tool_search")])


def test_register_and_unregister_advance_revision() -> None:
    registry = ToolRegistry()
    initial_revision = registry.revision

    registry.register(_tool("calendar"), permission=False)

    assert registry.revision == initial_revision + 1
    assert registry.get("calendar") is not None
    assert not registry.is_allowed("calendar", {})

    registry.unregister("calendar")

    assert registry.revision == initial_revision + 2
    assert registry.get("calendar") is None
