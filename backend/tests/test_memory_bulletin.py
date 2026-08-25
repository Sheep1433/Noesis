from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from noesis.services.memory.bulletin import MemoryBulletinService, render_bulletin


def _item(item_id: str, statement: str, *, memory_type="decision"):
    return SimpleNamespace(
        id=item_id,
        memory_type=memory_type,
        subject="Memory switch",
        statement=statement,
        applicability="Current repository",
        status="active",
        last_verified_at="dynamic-time",
        evidence_count=99,
        source_run_id="dynamic-run",
    )


def test_canonical_bulletin_is_byte_stable_and_excludes_dynamic_metadata() -> None:
    first_item = _item("memory-b", "Use one switch.")
    second_item = _item(
        "memory-a", "Capture successful runs.", memory_type="experience"
    )

    first = render_bulletin([(first_item, 0.82), (second_item, 0.84)], max_tokens=500)
    first_item.last_verified_at = "another-time"
    first_item.evidence_count = 1
    first_item.source_run_id = "another-run"
    second = render_bulletin([(second_item, 0.84), (first_item, 0.82)], max_tokens=500)

    assert first == second
    assert "dynamic" not in first.text
    assert "dynamic-run" not in first.text
    assert first.memory_ids == ("memory-b", "memory-a")
    assert len(first.bulletin_hash) == 64


def test_bulletin_budget_stops_before_partial_item() -> None:
    items = [(_item(f"memory-{index}", "x" * 600), 0.9) for index in range(5)]
    bulletin = render_bulletin(items, max_tokens=200)
    assert len(bulletin.memory_ids) <= 1
    assert "</task_memory>" in bulletin.text or bulletin.text == ""


@pytest.mark.asyncio
async def test_authoritative_failure_returns_zero_degraded_injection(
    monkeypatch,
) -> None:
    class Preference:
        def __init__(self, _db):
            pass

        async def is_enabled(self, _user_id):
            return True

    class Repository:
        def __init__(self, _db):
            pass

        async def lexical_candidates(self, **_kwargs):
            raise RuntimeError("postgres unavailable")

    monkeypatch.setattr(
        "noesis.services.memory.bulletin.MemoryPreferenceRepository", Preference
    )
    monkeypatch.setattr(
        "noesis.services.memory.bulletin.MachineMemoryRepository", Repository
    )

    bulletin = await MemoryBulletinService.build(
        SimpleNamespace(), user_id="user-1", scope_key="scope", query="memory"
    )

    assert bulletin.text == ""
    assert bulletin.memory_ids == ()
    assert bulletin.degraded is True


@pytest.mark.asyncio
async def test_disabled_user_gets_zero_injection_without_touching_retrieval(
    monkeypatch,
) -> None:
    class Preference:
        def __init__(self, _db):
            pass

        async def is_enabled(self, _user_id):
            return False

    class Repository:
        def __init__(self, _db):
            raise AssertionError("disabled preference must stop before retrieval")

    monkeypatch.setattr(
        "noesis.services.memory.bulletin.MemoryPreferenceRepository", Preference
    )
    monkeypatch.setattr(
        "noesis.services.memory.bulletin.MachineMemoryRepository", Repository
    )

    bulletin = await MemoryBulletinService.build(
        SimpleNamespace(), user_id="user-1", scope_key="scope", query="memory"
    )

    assert bulletin.text == ""
    assert bulletin.memory_ids == ()
    assert bulletin.degraded is False


@pytest.mark.asyncio
async def test_pg_filter_is_authoritative_after_semantic_candidates(
    monkeypatch,
) -> None:
    active = _item("memory-active", "Use one switch.")

    class Preference:
        def __init__(self, _db):
            pass

        async def is_enabled(self, _user_id):
            return True

    class Repository:
        def __init__(self, _db):
            pass

        async def lexical_candidates(self, **_kwargs):
            return []

        async def eligible_items_by_ids(self, **kwargs):
            assert kwargs["memory_ids"] == ["memory-stale", "memory-active"]
            return [active]

    class Index:
        async def search(self, **_kwargs):
            return [("memory-stale", 0.99), ("memory-active", 0.9)]

    monkeypatch.setattr(
        "noesis.services.memory.bulletin.MemoryPreferenceRepository", Preference
    )
    monkeypatch.setattr(
        "noesis.services.memory.bulletin.MachineMemoryRepository", Repository
    )
    bulletin = await MemoryBulletinService.build(
        SimpleNamespace(),
        user_id="user-1",
        scope_key="scope",
        query="switch",
        index=Index(),
    )
    assert bulletin.memory_ids == ("memory-active",)
    assert "memory-stale" not in bulletin.text


@pytest.mark.asyncio
async def test_manifest_candidate_does_not_receive_fixed_relevance_boost(
    monkeypatch,
) -> None:
    correct = _item("memory-correct", "Reproduce the failure before changing code.")
    correct.subject = "Diagnosis workflow"
    correct.applicability = "Failure diagnosis"
    generic = _item("memory-generic", "PostgreSQL is authoritative.")
    generic.subject = "Memory persistence"

    class Preference:
        def __init__(self, _db):
            pass

        async def is_enabled(self, _user_id):
            return True

    class Repository:
        def __init__(self, _db):
            pass

        async def lexical_candidates(self, **_kwargs):
            return []

        async def eligible_items_by_ids(self, **_kwargs):
            return [generic, correct]

    class Index:
        async def search(self, **_kwargs):
            return [(correct.id, 0.66), (generic.id, 0.50)]

    monkeypatch.setattr(
        "noesis.services.memory.bulletin.MemoryPreferenceRepository", Preference
    )
    monkeypatch.setattr(
        "noesis.services.memory.bulletin.MachineMemoryRepository", Repository
    )
    monkeypatch.setattr(
        "noesis.services.memory.bulletin.search_manifest_handles",
        lambda **_kwargs: [generic.id],
    )

    bulletin = await MemoryBulletinService.build(
        SimpleNamespace(),
        user_id="user-1",
        scope_key="scope",
        query="What workflow applies before code changes for a failure?",
        index=Index(),
    )

    assert bulletin.memory_ids == (correct.id,)


@pytest.mark.asyncio
async def test_slow_semantic_dependency_stops_within_bulletin_budget(
    monkeypatch,
) -> None:
    class Preference:
        def __init__(self, _db):
            pass

        async def is_enabled(self, _user_id):
            return True

    class Repository:
        def __init__(self, _db):
            pass

        async def lexical_candidates(self, **_kwargs):
            return []

    class SlowIndex:
        async def search(self, **_kwargs):
            await asyncio.sleep(1)

    from noesis.config.env import MachineMemoryConfig

    monkeypatch.setattr(
        "noesis.services.memory.bulletin.MemoryPreferenceRepository", Preference
    )
    monkeypatch.setattr(
        "noesis.services.memory.bulletin.MachineMemoryRepository", Repository
    )
    monkeypatch.setattr(
        "noesis.services.memory.bulletin.MachineMemoryConfig",
        replace(MachineMemoryConfig, bulletin_timeout_seconds=0.01),
    )

    bulletin = await MemoryBulletinService.build(
        SimpleNamespace(),
        user_id="user-1",
        scope_key="scope",
        query="memory",
        index=SlowIndex(),
    )

    assert bulletin.memory_ids == ()
    assert bulletin.degraded is True


@pytest.mark.asyncio
async def test_preference_store_failure_degrades_to_zero_injection(
    monkeypatch,
) -> None:
    class Preference:
        def __init__(self, _db):
            pass

        async def is_enabled(self, _user_id):
            raise RuntimeError("postgres unavailable")

    class Repository:
        def __init__(self, _db):
            raise AssertionError("degradation must happen before retrieval")

    monkeypatch.setattr(
        "noesis.services.memory.bulletin.MemoryPreferenceRepository", Preference
    )
    monkeypatch.setattr(
        "noesis.services.memory.bulletin.MachineMemoryRepository", Repository
    )

    bulletin = await MemoryBulletinService.build(
        SimpleNamespace(), user_id="user-1", scope_key="scope", query="memory"
    )

    assert bulletin.text == ""
    assert bulletin.memory_ids == ()
    assert bulletin.degraded is True
