"""Authenticated, scope-bound read-only memory tools."""

from __future__ import annotations

import json
import asyncio
from datetime import datetime

from langchain_core.tools import StructuredTool
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.schemas.memory import MemorySearchInput, MemorySourceInput
from noesis.services.memory.query import MemoryQueryService
from noesis.services.memory.scope import resolve_scope_key
from noesis.services.memory.source import MemorySourceService


def build_memory_tools(
    *,
    db: AsyncSession,
    user_id: str,
    session_id: str,
    agent_profile: str,
) -> list[StructuredTool]:
    scope_key = resolve_scope_key(
        user_id=user_id, session_id=session_id, agent_profile=agent_profile
    )
    query_lock = asyncio.Lock()

    async def search_memory(
        query: str,
        memory_types: list[str] | None = None,
        include_history: bool = False,
        statuses: list[str] | None = None,
        source_types: list[str] | None = None,
        project_scope: str = "current_project",  # noqa: ARG001 - schema locks scope
        expand_evidence: bool = True,
        since: datetime | None = None,
        until: datetime | None = None,
        top_k: int = 5,
    ) -> str:
        async with query_lock:
            result = await MemoryQueryService.search(
                db,
                user_id=user_id,
                scope_key=scope_key,
                query=query,
                memory_types=tuple(memory_types or ()),
                include_history=include_history,
                statuses=tuple(statuses or ()),
                source_types=tuple(source_types or ()),
                expand_evidence=expand_evidence,
                since=since,
                until=until,
                top_k=top_k,
            )
        return json.dumps(result.model_dump(mode="json"), ensure_ascii=False)

    async def get_memory_source(memory_id: str, evidence_id: str) -> str:
        try:
            async with query_lock:
                result = await MemorySourceService.get(
                    db,
                    user_id=user_id,
                    memory_id=memory_id,
                    evidence_id=evidence_id,
                    scope_key=scope_key,
                )
        except LookupError:
            return json.dumps({"error": "记忆来源不存在"}, ensure_ascii=False)
        return json.dumps(result.model_dump(mode="json"), ensure_ascii=False)

    return [
        StructuredTool.from_function(
            coroutine=search_memory,
            name="search_memory",
            description="Search scoped task experience and return bounded evidence references.",
            args_schema=MemorySearchInput,
        ),
        StructuredTool.from_function(
            coroutine=get_memory_source,
            name="get_memory_source",
            description="Read one bounded source span returned by memory search.",
            args_schema=MemorySourceInput,
        ),
    ]


__all__ = ["build_memory_tools"]
