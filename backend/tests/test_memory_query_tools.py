from __future__ import annotations

import json
import asyncio
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from noesis.agents.tools.memory_tools import build_memory_tools
from noesis.schemas.memory import MemoryDeepQueryResponse
from noesis.services.memory.query import MemoryQueryService, _record_query_trace


@pytest.mark.asyncio
async def test_query_abstains_when_no_evidence(monkeypatch) -> None:
    class Repository:
        def __init__(self, _db):
            pass

        async def search_items(self, **_kwargs):
            return []

        async def eligible_items_by_ids(self, **_kwargs):
            return []

    class Index:
        async def search(self, **_kwargs):
            return []

    monkeypatch.setattr("noesis.services.memory.query.MachineMemoryRepository", Repository)
    result = await MemoryQueryService.search(
        SimpleNamespace(),
        user_id="user-1",
        scope_key="scope",
        query="unknown history",
        index=Index(),
        record_trace=False,
    )
    assert result.evidence_status == "insufficient"
    assert result.memory_ids == []
    assert result.source_spans == []


@pytest.mark.asyncio
async def test_search_tool_binds_authenticated_user_and_runtime_scope(monkeypatch) -> None:
    captured = {}

    async def search(_db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            model_dump=lambda **_kwargs: {
                "bulletin": "evidence",
                "memory_ids": ["memory-1"],
                "source_spans": ["memory-1:evidence-1:message"],
                "evidence_status": "exact",
                "error": None,
            }
        )

    monkeypatch.setattr(MemoryQueryService, "search", search)
    monkeypatch.setattr(
        "noesis.agents.tools.memory_tools.resolve_scope_key", lambda *_a, **_k: "profile:SUPER_AGENT_QA|project:git-origin:abc"
    )
    tools = build_memory_tools(
        db=SimpleNamespace(),
        user_id="user-1",
        session_id="session-1",
        agent_profile="SUPER_AGENT_QA",
    )
    result = json.loads(await tools[0].ainvoke({"query": "memory switch"}))

    assert result["memory_ids"] == ["memory-1"]
    assert captured["user_id"] == "user-1"
    assert captured["scope_key"].startswith(
        "profile:SUPER_AGENT_QA|project:git-origin:abc"
    )
    assert "scope_key" not in tools[0].args


@pytest.mark.asyncio
async def test_source_tool_binds_same_runtime_scope(monkeypatch) -> None:
    captured = {}

    async def get(_db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(model_dump=lambda **_kwargs: {
            "memory_id": "memory-1",
            "evidence_id": "evidence-1",
            "availability": "available",
        })

    monkeypatch.setattr("noesis.agents.tools.memory_tools.MemorySourceService.get", get)
    monkeypatch.setattr(
        "noesis.agents.tools.memory_tools.resolve_scope_key", lambda *_a, **_k: "profile:SUPER_AGENT_QA|project:git-origin:abc"
    )
    tools = build_memory_tools(
        db=SimpleNamespace(),
        user_id="user-1",
        session_id="session-1",
        agent_profile="SUPER_AGENT_QA",
    )

    await tools[1].ainvoke({"memory_id": "memory-1", "evidence_id": "evidence-1"})

    assert captured["user_id"] == "user-1"
    assert captured["scope_key"].endswith("project:git-origin:abc")
    assert "scope_key" not in tools[1].args


@pytest.mark.asyncio
async def test_query_timeout_returns_bounded_error(monkeypatch) -> None:
    async def slow(*_args, **_kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(MemoryQueryService, "_search", slow)
    monkeypatch.setattr(
        "noesis.services.memory.query.MachineMemoryConfig",
        SimpleNamespace(deep_query_timeout_seconds=0.01),
    )
    result = await MemoryQueryService.search(
        SimpleNamespace(),
        user_id="user-1",
        scope_key="scope",
        query="slow",
        record_trace=False,
    )
    assert result.evidence_status == "unavailable"
    assert "超时" in (result.error or "")


@pytest.mark.asyncio
async def test_semantic_timeout_keeps_verified_lexical_result(monkeypatch) -> None:
    captured = {}
    item = SimpleNamespace(
        id="memory-1",
        memory_type="workflow",
        subject="bounded retry",
        statement="Retry once after timeout.",
        applicability="timeout",
        status="active",
        last_verified_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    class Repository:
        def __init__(self, _db):
            pass

        async def search_items(self, **kwargs):
            captured.update(kwargs)
            if kwargs["statuses"] == ("needs_review",):
                return []
            return [item]

        async def list_evidence_for_items(self, **_kwargs):
            return []

    class SlowIndex:
        async def search(self, **_kwargs):
            await asyncio.sleep(1)

    monkeypatch.setattr("noesis.services.memory.query.MachineMemoryRepository", Repository)
    monkeypatch.setattr(
        "noesis.services.memory.query.MachineMemoryConfig",
        SimpleNamespace(
            deep_query_timeout_seconds=0.02,
            deep_query_max_steps=6,
            deep_query_max_spans=12,
            bulletin_max_tokens=500,
            retrieval_min_score=0.45,
        ),
    )
    since = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = await MemoryQueryService.search(
        SimpleNamespace(),
        user_id="user-1",
        scope_key="scope-a",
        query="retry timeout",
        since=since,
        index=SlowIndex(),
        record_trace=False,
    )

    assert result.memory_ids == ["memory-1"]
    assert result.evidence_status in {"near", "exact"}
    assert captured["user_id"] == "user-1"
    assert captured["scope_key"] == "scope-a"
    assert captured["since"] == since


@pytest.mark.asyncio
async def test_query_reports_matching_review_conflict_as_contradiction(monkeypatch) -> None:
    active = SimpleNamespace(
        id="memory-active",
        memory_type="decision",
        subject="memory switch",
        statement="Use one memory switch.",
        applicability="settings",
        status="active",
    )
    conflict = SimpleNamespace(
        id="memory-conflict",
        memory_type="decision",
        subject="memory switch",
        statement="The switch design has conflicting evidence.",
        applicability="settings",
        status="needs_review",
    )

    class Repository:
        def __init__(self, _db):
            pass

        async def search_items(self, **kwargs):
            return [active, conflict]

        async def list_evidence_for_items(self, **_kwargs):
            return []

    class Index:
        async def search(self, **_kwargs):
            return []

    monkeypatch.setattr("noesis.services.memory.query.MachineMemoryRepository", Repository)
    result = await MemoryQueryService.search(
        SimpleNamespace(),
        user_id="user-1",
        scope_key="scope",
        query="use two memory switches",
        index=Index(),
        record_trace=False,
    )

    assert result.evidence_status == "contradicts"
    assert result.memory_ids == ["memory-active"]
    assert all(item.status == "active" for item in result.items)


@pytest.mark.asyncio
async def test_query_trace_persists_bounded_metrics_without_query_text(monkeypatch) -> None:
    added = []
    db = SimpleNamespace(add=added.append, commit=AsyncMock())

    @asynccontextmanager
    async def context():
        yield db

    monkeypatch.setattr(
        "noesis.services.memory.query.pg_manager.get_async_session_context",
        context,
    )
    await _record_query_trace(
        user_id="user-1",
        scope_key="scope",
        duration_ms=12,
        result=MemoryDeepQueryResponse(
            bulletin="bounded result",
            memory_ids=["memory-1"],
            source_spans=["memory-1:evidence-1:message"],
            evidence_status="exact",
        ),
        input_tokens=8,
        steps=4,
    )

    assert len(added) == 1
    assert added[0].steps == 4
    assert added[0].returned_spans == 1
    assert not hasattr(added[0], "query")
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_query_enforces_step_budget_before_reading(monkeypatch) -> None:
    monkeypatch.setattr(
        "noesis.services.memory.query.MachineMemoryConfig",
        SimpleNamespace(
            deep_query_timeout_seconds=1,
            deep_query_max_steps=3,
        ),
    )
    result = await MemoryQueryService.search(
        SimpleNamespace(),
        user_id="user-1",
        scope_key="scope",
        query="memory",
        record_trace=False,
    )
    assert result.evidence_status == "unavailable"
    assert "预算" in (result.error or "")


@pytest.mark.asyncio
async def test_evidence_dependency_failure_returns_unavailable(monkeypatch) -> None:
    item = SimpleNamespace(
        id="memory-1",
        memory_type="decision",
        subject="memory switch",
        statement="Use one switch.",
        applicability="settings",
        status="active",
    )

    class Repository:
        def __init__(self, _db):
            pass

        async def search_items(self, **_kwargs):
            return [item]

        async def list_evidence_for_items(self, **_kwargs):
            raise RuntimeError("database unavailable")

    class Index:
        async def search(self, **_kwargs):
            return []

    monkeypatch.setattr("noesis.services.memory.query.MachineMemoryRepository", Repository)
    result = await MemoryQueryService.search(
        SimpleNamespace(),
        user_id="user-1",
        scope_key="scope",
        query="switch",
        index=Index(),
        record_trace=False,
    )
    assert result.evidence_status == "unavailable"
    assert "来源" in (result.error or "")


@pytest.mark.asyncio
async def test_query_drops_semantic_candidates_below_relevance_floor(monkeypatch) -> None:
    item = SimpleNamespace(
        id="memory-noise",
        memory_type="workflow",
        subject="unrelated subject",
        statement="Unrelated statement.",
        applicability="different context",
        status="active",
    )

    class Repository:
        def __init__(self, _db):
            pass

        async def search_items(self, **_kwargs):
            return []

        async def eligible_items_by_ids(self, **_kwargs):
            return [item]

    class Index:
        async def search(self, **_kwargs):
            return [(item.id, 0.44)]

    monkeypatch.setattr("noesis.services.memory.query.MachineMemoryRepository", Repository)
    result = await MemoryQueryService.search(
        SimpleNamespace(),
        user_id="user-1",
        scope_key="scope",
        query="target query",
        index=Index(),
        record_trace=False,
    )

    assert result.evidence_status == "insufficient"
    assert result.memory_ids == []


@pytest.mark.asyncio
async def test_history_status_filter_is_not_bypassed_by_semantic_active(monkeypatch) -> None:
    disabled = SimpleNamespace(
        id="memory-disabled",
        memory_type="decision",
        subject="old switch",
        statement="Old disabled decision.",
        applicability="settings",
        status="disabled",
    )

    class Repository:
        def __init__(self, _db):
            pass

        async def search_items(self, **kwargs):
            assert kwargs["statuses"] == ("disabled",)
            return [disabled]

        async def list_evidence_for_items(self, **_kwargs):
            return []

        async def eligible_items_by_ids(self, **_kwargs):
            raise AssertionError("active semantic items must not bypass disabled filter")

    class Index:
        async def search(self, **_kwargs):
            return [("memory-active", 0.99)]

    monkeypatch.setattr("noesis.services.memory.query.MachineMemoryRepository", Repository)
    result = await MemoryQueryService.search(
        SimpleNamespace(),
        user_id="user-1",
        scope_key="scope",
        query="switch",
        include_history=True,
        statuses=("disabled",),
        index=Index(),
        record_trace=False,
    )
    assert result.memory_ids == ["memory-disabled"]
    assert result.items[0].status == "disabled"


@pytest.mark.asyncio
async def test_source_type_filters_items_even_without_expanding_spans(monkeypatch) -> None:
    items = [
        SimpleNamespace(
            id=f"memory-{index}",
            memory_type="experience",
            subject="retry",
            statement="Retry after timeout.",
            applicability="timeout",
            status="active",
        )
        for index in (1, 2)
    ]

    class Repository:
        def __init__(self, _db):
            pass

        async def search_items(self, **_kwargs):
            return items

        async def source_types_for_items(self, **kwargs):
            assert kwargs["source_types"] == ("tool",)
            return {"memory-1": {"tool"}}

    class Index:
        async def search(self, **_kwargs):
            return []

    monkeypatch.setattr("noesis.services.memory.query.MachineMemoryRepository", Repository)
    result = await MemoryQueryService.search(
        SimpleNamespace(),
        user_id="user-1",
        scope_key="scope",
        query="retry",
        source_types=("tool",),
        expand_evidence=False,
        index=Index(),
        record_trace=False,
    )
    assert result.memory_ids == ["memory-1"]
    assert result.source_spans == []
    assert result.items[0].source_types == ["tool"]
