"""Tool catalog middleware — deferred tool schema + tool search.

Computes the token cost of all tool schemas, keeps base/activated tools always
bound, and defers large MCP/extension tool schemas behind a ``tool_search``
tool. This is the Noesis owner for the Claude Code "tool catalog / deferred
schema" behaviour; LangChain's ``tool_selection`` is a one-shot pre-call
LLM selector with a different semantics and no runtime ``tool_search`` +
discovered-set lifecycle.

Design contract (``simplify-agent-context-architecture`` §11):

- compute the token cost of all tool schemas;
- base tools and currently-activated tools are always bound;
- large MCP/extension tools are marked deferred — their full schema is not
  sent to the model by default;
- a ``tool_search`` tool adds matching deferred schemas to the current run's
  discovered set;
- MCP connection changes form a bounded delta; the catalog is rebuilt after
  compaction;
- when the provider natively supports deferred tools, use the native field;
  otherwise the middleware filters ``request.tools`` dynamically.

This implementation covers the deferred-filtering + ``tool_search`` +
discovered-set lifecycle (the well-defined core). The MCP-connection delta is
sourced from an injected ``catalog_provider`` so the middleware makes no
runtime calls.

Self-containment: depends only on the injected catalog, base-tool predicate
and schema token counter; discovered set lives in private state.
"""

from __future__ import annotations

import json
import logging
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
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

if TYPE_CHECKING:
    from collections.abc import Awaitable

logger = logging.getLogger(__name__)


def _schema_token_cost(tool: BaseTool | dict[str, Any]) -> int:
    """Cheap token proxy for a tool schema: JSON length / 4."""
    if isinstance(tool, BaseTool):
        schema = tool.args_schema.model_json_schema() if getattr(tool, "args_schema", None) else {}
        name = tool.name
        desc = tool.description or ""
    elif isinstance(tool, dict):
        schema = tool.get("args", {}) or {}
        name = tool.get("name", "")
        desc = tool.get("description", "") or ""
    else:
        return 0
    payload = json.dumps({"name": name, "desc": desc, "schema": schema}, default=str, ensure_ascii=False)
    return len(payload) // 4


def _tool_name(tool: Any) -> str:
    if isinstance(tool, BaseTool):
        return tool.name
    if isinstance(tool, dict):
        return str(tool.get("name", ""))
    return getattr(tool, "name", "")


@dataclass
class ToolCatalogEntry:
    name: str
    tool: Any
    cost: int
    deferred: bool


def _default_basic_predicate(name: str) -> bool:
    """Default base-tool predicate — common always-bound tools."""
    return name in {
        "read_file", "write_file", "edit_file", "ls", "glob", "grep",
        "execute", "task", "write_todos", "tool_search",
    }


class ToolCatalogMiddleware(AgentMiddleware[AgentState[ResponseT], ContextT, ResponseT]):
    """Defer oversized tool schemas behind ``tool_search`` and manage discovered set."""

    def __init__(
        self,
        *,
        schema_budget_tokens: int = 8_000,
        large_schema_cost: int = 200,
        basic_predicate: Callable[[str], bool] | None = None,
        catalog_provider: Callable[[], list[Any]] | None = None,
    ) -> None:
        self._budget = max(1, schema_budget_tokens)
        self._large_cost = max(1, large_schema_cost)
        self._is_basic = basic_predicate or _default_basic_predicate
        self._catalog_provider = catalog_provider

    @staticmethod
    def _discovered(state: AgentState[Any]) -> set[str]:
        return set(state.get("_tool_catalog_discovered", []))  # type: ignore[arg-type]

    @staticmethod
    def _commit_discovered(state: AgentState[Any], names: set[str]) -> None:
        state["_tool_catalog_discovered"] = sorted(names)  # type: ignore[assignment]

    def _entries(self, tools: list[Any]) -> list[ToolCatalogEntry]:
        """Build catalog entries from a tools list (request.tools or catalog)."""
        entries: list[ToolCatalogEntry] = []
        for tool in tools:
            name = _tool_name(tool)
            if not name:
                continue
            cost = _schema_token_cost(tool)
            deferred = not self._is_basic(name) and cost >= self._large_cost
            entries.append(ToolCatalogEntry(name=name, tool=tool, cost=cost, deferred=deferred))
        return entries

    def _entries_for(self, request: ModelRequest[ContextT]) -> list[ToolCatalogEntry]:
        tools = list(request.tools or [])
        if self._catalog_provider is not None:
            catalog_tools = list(self._catalog_provider())
            existing_names = {_tool_name(t) for t in tools}
            for t in catalog_tools:
                if _tool_name(t) and _tool_name(t) not in existing_names:
                    tools.append(t)
        return self._entries(tools)

    def _catalog_entries(self, state: AgentState[Any]) -> list[ToolCatalogEntry]:
        if self._catalog_provider is None:
            return []
        return self._entries(list(self._catalog_provider()))

    def _select_tools(
        self,
        entries: list[ToolCatalogEntry],
        discovered: set[str],
    ) -> list[Any]:
        bound: list[Any] = []
        total = 0
        # Always bind base + already-discovered tools first.
        for entry in entries:
            if self._is_basic(entry.name) or entry.name in discovered or not entry.deferred:
                bound.append(entry.tool)
                total += entry.cost
        # Then greedily add small (non-deferred) tools within budget.
        for entry in entries:
            if entry in bound:
                continue
            if entry.deferred:
                continue
            if total + entry.cost <= self._budget:
                bound.append(entry.tool)
                total += entry.cost
        # De-dup preserving order.
        seen: set[str] = set()
        result: list[Any] = []
        for tool in bound:
            name = _tool_name(tool)
            if name in seen:
                continue
            seen.add(name)
            result.append(tool)
        return result

    def _make_tool_search_tool(self, entries: list[ToolCatalogEntry]) -> BaseTool:
        middleware = self

        def tool_search(query: str) -> str:
            """Search deferred tools and activate matching schemas for this run."""
            return middleware._on_tool_search(query, entries)

        return StructuredTool.from_function(
            name="tool_search",
            func=tool_search,
            description="Search deferred MCP/extension tools and activate matching schemas for the current run.",
        )

    def _on_tool_search(self, query: str, entries: list[ToolCatalogEntry]) -> str:
        q = query.lower().strip()
        matched = [e for e in entries if e.deferred and q in e.name.lower()]
        if not matched:
            return f"No deferred tools matched '{query}'."
        names = sorted(e.name for e in matched)
        return f"Activated {len(matched)} tool schema(s): {', '.join(names)}. They are now available for use."

    # -- tool-call seam: record tool_search activations -------------------

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> Any:  # type: ignore[override]
        result = handler(request)
        if str(request.tool_call.get("name") or "") == "tool_search":
            query = str((request.tool_call.get("args") or {}).get("query", ""))
            entries = self._catalog_entries(request.state)
            matched = {
                e.name for e in entries
                if e.deferred and query.lower().strip() in e.name.lower()
            }
            if matched:
                discovered = self._discovered(request.state) | matched
                self._commit_discovered(request.state, discovered)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:  # type: ignore[override]
        result = await handler(request)
        if str(request.tool_call.get("name") or "") == "tool_search":
            query = str((request.tool_call.get("args") or {}).get("query", ""))
            entries = self._catalog_entries(request.state)
            matched = {
                e.name for e in entries
                if e.deferred and query.lower().strip() in e.name.lower()
            }
            if matched:
                discovered = self._discovered(request.state) | matched
                self._commit_discovered(request.state, discovered)
        return result

    # -- model-call seam: filter request.tools ----------------------------

    def modify_request(self, request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
        entries = self._entries_for(request)
        discovered = self._discovered(request.state)
        selected = self._select_tools(entries, discovered)
        has_deferred = any(e.deferred for e in entries)
        if has_deferred and not any(_tool_name(t) == "tool_search" for t in selected):
            selected.append(self._make_tool_search_tool(entries))
        original_names = [_tool_name(t) for t in (request.tools or [])]
        if [t.name if isinstance(t, BaseTool) else _tool_name(t) for t in selected] == original_names:
            return request
        return request.override(tools=selected)

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
    "ToolCatalogEntry",
    "ToolCatalogMiddleware",
    "_schema_token_cost",
]
