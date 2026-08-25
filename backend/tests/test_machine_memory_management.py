from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import inspect

import pytest

from noesis.errors.exceptions import ConflictException, NotFoundException
from noesis.services.memory.management import MachineMemoryService


def _item(status="active"):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id="memory-1",
        user_id="user-1",
        scope_key="profile:SUPER_AGENT_QA|project:global",
        memory_type="decision",
        status=status,
        subject="Memory switch",
        subject_key="a" * 64,
        statement="Use one switch.",
        applicability="Machine memory",
        content_digest="b" * 64,
        effective_provenance="user",
        version=1,
        valid_from=now,
        valid_to=None,
        last_verified_at=now,
        user_revision=False,
    )


@pytest.mark.asyncio
async def test_state_transitions_are_user_scoped_and_do_not_revive_invalidated(
    monkeypatch,
) -> None:
    item = _item("disabled")
    repository = SimpleNamespace(
        get_item_for_update=AsyncMock(return_value=item),
        add_desired_state_events=MagicMock(),
    )
    monkeypatch.setattr(
        "noesis.services.memory.management.MachineMemoryRepository",
        lambda _db: repository,
    )
    db = SimpleNamespace(commit=AsyncMock())

    enabled = await MachineMemoryService.change_state(
        db, user_id="user-1", memory_id="memory-1", operation="enable"
    )
    assert enabled.status == "active"
    assert item.user_revision is True
    db.commit.assert_awaited_once()

    item.status = "invalidated"
    with pytest.raises(ConflictException):
        await MachineMemoryService.change_state(
            db, user_id="user-1", memory_id="memory-1", operation="enable"
        )


@pytest.mark.asyncio
async def test_missing_item_is_reported_as_not_found(monkeypatch) -> None:
    repository = SimpleNamespace(get_item_for_update=AsyncMock(return_value=None))
    monkeypatch.setattr(
        "noesis.services.memory.management.MachineMemoryRepository",
        lambda _db: repository,
    )
    with pytest.raises(NotFoundException):
        await MachineMemoryService.delete(
            SimpleNamespace(), user_id="other-user", memory_id="memory-1"
        )


@pytest.mark.asyncio
async def test_list_items_batches_evidence_and_counts_distinct_runs(
    monkeypatch,
) -> None:
    items = [_item(), _item()]
    items[1].id = "memory-2"
    evidence = SimpleNamespace(
        id="evidence-1",
        source_kind="message",
        provenance="user",
        created_at=datetime.now(timezone.utc),
    )
    repository = SimpleNamespace(
        list_user_items=AsyncMock(return_value=items),
        list_evidence_by_items=AsyncMock(
            return_value={"memory-1": [evidence], "memory-2": [evidence]}
        ),
        count_evidence_runs=AsyncMock(return_value={"memory-1": 2, "memory-2": 1}),
        list_item_evidence=AsyncMock(),
    )
    monkeypatch.setattr(
        "noesis.services.memory.management.MachineMemoryRepository",
        lambda _db: repository,
    )

    result = await MachineMemoryService.list_items(SimpleNamespace(), user_id="user-1")

    assert [item.evidence_count for item in result] == [2, 1]
    repository.list_evidence_by_items.assert_awaited_once()
    repository.count_evidence_runs.assert_awaited_once()
    repository.list_item_evidence.assert_not_awaited()


def test_memory_router_exposes_governance_without_daily_routes() -> None:
    from server.api.user_settings_api import user_settings_router

    paths = {route.path for route in user_settings_router.routes}
    assert "/api/user/memory/cortex/preferences" in paths
    assert "/api/user/memory/cortex/items" in paths
    assert "/api/user/memory/cortex/items/{memory_id}" in paths
    assert "/api/user/memory/cortex/health" in paths
    assert not any("/memory/dream" in path or "/memory/daily" in path for path in paths)


def test_cortex_routes_require_auth_and_mutations_require_csrf() -> None:
    from server.api.user_settings_api import user_settings_router

    routes = [
        route
        for route in user_settings_router.routes
        if route.path.startswith("/api/user/memory/cortex")
    ]
    assert routes
    for route in routes:
        dependencies = {
            dependency.call.__name__
            for dependency in route.dependant.dependencies
            if dependency.call is not None
        }
        assert "get_current_user" in dependencies
        if set(route.methods or ()) & {"POST", "PUT", "DELETE"}:
            source = inspect.getsource(route.endpoint)
            if route.endpoint.__name__ in {
                "activate_machine_memory",
                "disable_machine_memory",
                "enable_machine_memory",
                "invalidate_machine_memory",
            }:
                source += inspect.getsource(
                    __import__(
                        "server.api.user_settings_api",
                        fromlist=["_change_machine_memory_state"],
                    )._change_machine_memory_state
                )
            assert "require_csrf" in source


@pytest.mark.asyncio
async def test_activate_confirms_candidate_as_user_verified(monkeypatch) -> None:
    item = _item("candidate")
    repository = SimpleNamespace(
        get_item_for_update=AsyncMock(return_value=item),
        add_desired_state_events=MagicMock(),
    )
    monkeypatch.setattr(
        "noesis.services.memory.management.MachineMemoryRepository",
        lambda _db: repository,
    )
    db = SimpleNamespace(commit=AsyncMock())

    activated = await MachineMemoryService.change_state(
        db, user_id="user-1", memory_id="memory-1", operation="activate"
    )
    assert activated.status == "active"
    assert item.user_revision is True
    assert item.effective_provenance == "user"
    assert item.valid_to is None
    db.commit.assert_awaited_once()

    item.status = "disabled"
    with pytest.raises(ConflictException):
        await MachineMemoryService.change_state(
            db, user_id="user-1", memory_id="memory-1", operation="activate"
        )
