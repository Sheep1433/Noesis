"""Authoritative runtime registry for tools, schemas, revisions, and permissions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, TypeAlias

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langchain_core.tools.base import InjectedToolCallId
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

PermissionCheck: TypeAlias = bool | Callable[[Mapping[str, Any]], bool]
CatalogProvider: TypeAlias = Callable[[], Iterable[BaseTool]]

PROMOTIONS_STATE_KEY = "_tool_catalog_discovered"
TOOL_SEARCH_NAME = "tool_search"


def _default_basic_predicate(name: str) -> bool:
    return name in {
        "read_file",
        "write_file",
        "edit_file",
        "ls",
        "glob",
        "grep",
        "execute",
        "task",
        "write_todos",
        TOOL_SEARCH_NAME,
    }


def tool_schema(tool: BaseTool) -> dict[str, Any]:
    """Return the exact model-facing schema, excluding injected arguments."""
    schema_model = tool.tool_call_schema
    args = schema_model.model_json_schema() if schema_model is not None else {}
    return {
        "name": tool.name,
        "description": tool.description or "",
        "args": args,
    }


def schema_token_cost(tool: BaseTool) -> int:
    payload = json.dumps(tool_schema(tool), sort_keys=True, default=str, ensure_ascii=False)
    return len(payload) // 4


@dataclass(frozen=True)
class ToolCatalogEntry:
    name: str
    tool: BaseTool
    schema: Mapping[str, Any]
    cost: int
    deferred: bool
    permission: PermissionCheck


@dataclass(frozen=True)
class ToolCatalogSnapshot:
    revision: int
    catalog_hash: str
    entries: tuple[ToolCatalogEntry, ...]

    def entry(self, name: str) -> ToolCatalogEntry | None:
        return next((entry for entry in self.entries if entry.name == name), None)


class ToolRegistry:
    """Own a catalog and its stateful deferred-schema promotion contract."""

    def __init__(
        self,
        tools: Iterable[BaseTool] = (),
        *,
        permissions: Mapping[str, PermissionCheck] | None = None,
        catalog_provider: CatalogProvider | None = None,
        schema_budget_tokens: int = 8_000,
        large_schema_cost: int = 200,
        basic_predicate: Callable[[str], bool] | None = None,
    ) -> None:
        self._provider = catalog_provider
        self._permissions = dict(permissions or {})
        self._budget = max(1, schema_budget_tokens)
        self._large_cost = max(1, large_schema_cost)
        self._is_basic = basic_predicate or _default_basic_predicate
        self._explicit_tools = list(tools)
        self._request_tools: list[BaseTool] = []
        self._revision = 0
        self._snapshot = ToolCatalogSnapshot(0, self._hash_entries(()), ())
        self._tool_search = self._create_tool_search()
        self.refresh()

    @property
    def snapshot(self) -> ToolCatalogSnapshot:
        return self._snapshot

    @property
    def revision(self) -> int:
        return self._snapshot.revision

    @property
    def catalog_hash(self) -> str:
        return self._snapshot.catalog_hash

    @property
    def tool_search(self) -> BaseTool:
        return self._tool_search

    def refresh(self, request_tools: Iterable[BaseTool] | None = None) -> ToolCatalogSnapshot:
        """Refresh one catalog snapshot from registered, provider, and request tools."""
        if request_tools is not None:
            self._request_tools = list(request_tools)
        tools = [*self._explicit_tools]
        if self._provider is not None:
            tools.extend(self._provider())
            tools.extend(tool for tool in self._request_tools if self._is_basic(tool.name))
        else:
            tools.extend(self._request_tools)
        entries = self._build_entries(self._deduplicate(tools))
        catalog_hash = self._hash_entries(entries)
        if catalog_hash != self._snapshot.catalog_hash:
            self._revision += 1
        self._snapshot = ToolCatalogSnapshot(self._revision, catalog_hash, entries)
        return self._snapshot

    def register(self, tool: BaseTool, *, permission: PermissionCheck = True) -> None:
        """Register or replace one explicit tool and advance the catalog revision."""
        if tool.name == TOOL_SEARCH_NAME:
            raise ValueError("tool_search is reserved by ToolRegistry")
        self._explicit_tools = [item for item in self._explicit_tools if item.name != tool.name]
        self._explicit_tools.append(tool)
        self._permissions[tool.name] = permission
        self.refresh()

    def unregister(self, name: str) -> None:
        """Remove one explicit tool; provider/request-owned tools remain authoritative."""
        self._explicit_tools = [tool for tool in self._explicit_tools if tool.name != name]
        self._permissions.pop(name, None)
        self.refresh()

    def get(self, name: str) -> BaseTool | None:
        entry = self._snapshot.entry(name)
        return entry.tool if entry is not None else None

    def schema_for(self, name: str) -> Mapping[str, Any] | None:
        entry = self._snapshot.entry(name)
        return entry.schema if entry is not None else None

    def promoted_names(self, state: Mapping[str, Any]) -> set[str]:
        promotions = state.get(PROMOTIONS_STATE_KEY, {})
        if not isinstance(promotions, Mapping):
            return set()
        names = promotions.get(self.catalog_hash, ())
        return {str(name) for name in names} if isinstance(names, (list, tuple, set)) else set()

    def visible_tools(self, state: Mapping[str, Any]) -> list[BaseTool]:
        promoted = self.promoted_names(state)
        has_deferred = any(entry.deferred for entry in self._snapshot.entries)
        return [
            entry.tool
            for entry in self._snapshot.entries
            if (not entry.deferred or entry.name in promoted)
            and (entry.name != TOOL_SEARCH_NAME or has_deferred)
        ]

    def is_allowed(self, name: str, state: Mapping[str, Any]) -> bool:
        entry = self._snapshot.entry(name)
        if entry is None:
            return False
        permission = entry.permission
        return permission(state) if callable(permission) else permission

    def assert_executable(self, name: str, state: Mapping[str, Any]) -> None:
        self.refresh()
        entry = self._snapshot.entry(name)
        if entry is None:
            raise PermissionError(f"tool '{name}' is not registered")
        if entry.deferred and name not in self.promoted_names(state):
            raise PermissionError(f"tool '{name}' schema has not been promoted for this catalog")
        if not self.is_allowed(name, state):
            raise PermissionError(f"tool '{name}' is not permitted")

    def _promotion_result(
        self,
        query: str,
        state: Mapping[str, Any],
    ) -> tuple[str, dict[str, list[str]] | None]:
        normalized = query.casefold().strip()
        matched = [
            entry
            for entry in self._snapshot.entries
            if entry.deferred
            and normalized in f"{entry.name} {entry.tool.description or ''}".casefold()
            and self.is_allowed(entry.name, state)
        ]
        if not matched:
            return f"No deferred tools matched '{query}'.", None

        promotions = state.get(PROMOTIONS_STATE_KEY)
        by_hash = dict(promotions) if isinstance(promotions, Mapping) else {}
        names = self.promoted_names(state) | {entry.name for entry in matched}
        by_hash[self.catalog_hash] = sorted(names)
        matched_names = ", ".join(sorted(entry.name for entry in matched))
        return f"Promoted {len(matched)} tool schema(s): {matched_names}.", by_hash

    def _create_tool_search(self) -> BaseTool:
        registry = self

        def tool_search(
            query: str,
            state: Annotated[dict[str, Any], InjectedState],
            tool_call_id: Annotated[str, InjectedToolCallId],
        ) -> Command[Any]:
            """Search deferred tools and promote matching schemas for this run."""
            return registry._promotion_command(query, state, tool_call_id)

        async def atool_search(
            query: str,
            state: Annotated[dict[str, Any], InjectedState],
            tool_call_id: Annotated[str, InjectedToolCallId],
        ) -> Command[Any]:
            return registry._promotion_command(query, state, tool_call_id)

        return StructuredTool.from_function(
            func=tool_search,
            coroutine=atool_search,
            name=TOOL_SEARCH_NAME,
            description="Search deferred tools and promote matching schemas for the current run.",
        )

    def _promotion_command(
        self,
        query: str,
        state: Mapping[str, Any],
        tool_call_id: str,
    ) -> Command[Any]:
        message, promotions = self._promotion_result(query, state)
        update: dict[str, Any] = {
            "messages": [
                ToolMessage(
                    content=message,
                    name=TOOL_SEARCH_NAME,
                    tool_call_id=tool_call_id,
                )
            ]
        }
        if promotions is not None:
            update[PROMOTIONS_STATE_KEY] = promotions
        return Command(update=update)

    def _build_entries(self, tools: tuple[BaseTool, ...]) -> tuple[ToolCatalogEntry, ...]:
        records: list[tuple[BaseTool, Mapping[str, Any], int]] = []
        for tool in tools:
            schema = tool_schema(tool)
            records.append((tool, schema, schema_token_cost(tool)))

        total = 0
        entries: list[ToolCatalogEntry] = []
        for tool, schema, cost in records:
            basic = self._is_basic(tool.name)
            deferred = not basic and (cost >= self._large_cost or total + cost > self._budget)
            if not deferred:
                total += cost
            entries.append(
                ToolCatalogEntry(
                    name=tool.name,
                    tool=tool,
                    schema=schema,
                    cost=cost,
                    deferred=deferred,
                    permission=self._permissions.get(tool.name, True),
                )
            )

        search_schema = tool_schema(self._tool_search)
        entries.append(
            ToolCatalogEntry(
                name=TOOL_SEARCH_NAME,
                tool=self._tool_search,
                schema=search_schema,
                cost=schema_token_cost(self._tool_search),
                deferred=False,
                permission=True,
            )
        )
        return tuple(entries)

    def _deduplicate(self, tools: Iterable[BaseTool]) -> tuple[BaseTool, ...]:
        by_name: dict[str, BaseTool] = {}
        for tool in tools:
            if not isinstance(tool, BaseTool):
                raise TypeError("ToolRegistry accepts registered BaseTool instances only")
            if tool.name == TOOL_SEARCH_NAME:
                if tool is self._tool_search:
                    continue
                raise ValueError("tool_search is reserved by ToolRegistry")
            by_name.setdefault(tool.name, tool)
        return tuple(by_name.values())

    @staticmethod
    def _hash_entries(entries: Iterable[ToolCatalogEntry]) -> str:
        payload = [
            {
                "name": entry.name,
                "schema": entry.schema,
                "deferred": entry.deferred,
                "permission": ToolRegistry._permission_fingerprint(entry.permission),
            }
            for entry in entries
        ]
        encoded = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _permission_fingerprint(permission: PermissionCheck) -> str | bool:
        if not callable(permission):
            return permission
        return f"{permission.__module__}.{getattr(permission, '__qualname__', permission.__class__.__qualname__)}"


__all__ = [
    "PROMOTIONS_STATE_KEY",
    "TOOL_SEARCH_NAME",
    "PermissionCheck",
    "ToolCatalogEntry",
    "ToolCatalogSnapshot",
    "ToolRegistry",
    "schema_token_cost",
    "tool_schema",
]
