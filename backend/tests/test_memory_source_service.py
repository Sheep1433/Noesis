from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from noesis.schemas.memory import MemorySourceSpan, RunSnapshotPayload
from noesis.services.memory.source import MemorySourceService
from noesis.services.user_service import UserService


def _payload():
    span = MemorySourceSpan(
        id="span-1",
        source_ref="message:user-message",
        kind="user_correction",
        provenance="user",
        effective_provenance="user",
        text="Use one switch.",
        digest="a" * 64,
    )
    return RunSnapshotPayload(
        run_id="run-1",
        user_id="user-1",
        session_id="session-1",
        scope_key="profile:SUPER_AGENT_QA|project:global",
        source_watermark=1,
        spans=[span],
        content_digest="b" * 64,
        token_estimate=10,
    )


@pytest.mark.asyncio
async def test_source_lookup_returns_bounded_snapshot_span(monkeypatch) -> None:
    evidence = SimpleNamespace(
        id="evidence-1",
        snapshot_id="snapshot-1",
        source_kind="message",
        source_ref="message:user-message",
        span_digest="a" * 64,
    )
    repository = SimpleNamespace(
        get_evidence_for_user=AsyncMock(return_value=(SimpleNamespace(), evidence)),
        get_snapshot=AsyncMock(return_value=SimpleNamespace(
            run_id="run-1",
            evidence_json=_payload().model_dump(mode="json"),
            captured_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )),
    )
    monkeypatch.setattr(
        "noesis.services.memory.source.MachineMemoryRepository", lambda _db: repository
    )
    objects = {
        ("TAgentRun", "run-1"): SimpleNamespace(assistant_message_id="assistant-message"),
        ("TChatMessage", "assistant-message"): SimpleNamespace(deleted_at=None),
        ("TChatMessage", "user-message"): SimpleNamespace(deleted_at=None),
    }

    async def get(model, object_id):
        return objects[(model.__name__, object_id)]

    result = await MemorySourceService.get(
        SimpleNamespace(get=get),
        user_id="user-1",
        memory_id="memory-1",
        evidence_id="evidence-1",
    )

    assert result.availability == "available"
    assert result.excerpt == "Use one switch."
    assert result.provenance == "user"
    assert result.source_digest == "a" * 64
    assert result.role == "user"


@pytest.mark.asyncio
async def test_soft_deleted_message_returns_unavailable_without_excerpt(monkeypatch) -> None:
    evidence = SimpleNamespace(
        id="evidence-1",
        snapshot_id="snapshot-1",
        source_kind="message",
        source_ref="message:user-message",
        span_digest="a" * 64,
    )
    repository = SimpleNamespace(
        get_evidence_for_user=AsyncMock(return_value=(SimpleNamespace(), evidence)),
        get_snapshot=AsyncMock(return_value=SimpleNamespace(
            run_id="run-1",
            evidence_json=_payload().model_dump(mode="json"),
            captured_at=datetime.now(timezone.utc),
        )),
    )
    monkeypatch.setattr(
        "noesis.services.memory.source.MachineMemoryRepository", lambda _db: repository
    )

    async def get(model, object_id):
        if model.__name__ == "TAgentRun":
            return SimpleNamespace(assistant_message_id="assistant-message")
        return SimpleNamespace(deleted_at=object_id == "user-message")

    result = await MemorySourceService.get(
        SimpleNamespace(get=get),
        user_id="user-1",
        memory_id="memory-1",
        evidence_id="evidence-1",
    )

    assert result.availability == "source_deleted"
    assert result.excerpt is None


@pytest.mark.asyncio
async def test_account_cleanup_removes_authoritative_and_workspace_data(monkeypatch) -> None:
    repository = SimpleNamespace(delete_user_data=AsyncMock())
    remove_workspace = MagicMock()
    monkeypatch.setattr(
        "noesis.services.memory.source.MachineMemoryRepository", lambda _db: repository
    )
    monkeypatch.setattr(
        "noesis.services.memory.source.MemoryWorkspaceService.remove_user_workspace",
        remove_workspace,
    )
    index = SimpleNamespace(delete_user=AsyncMock())
    monkeypatch.setattr(
        "noesis.services.memory.source.MemoryIndexService", lambda: index
    )
    db = SimpleNamespace(commit=AsyncMock())

    await MemorySourceService.delete_user_data(db, user_id="user-1")

    index.delete_user.assert_awaited_once_with("user-1")
    repository.delete_user_data.assert_awaited_once_with("user-1")
    db.commit.assert_awaited_once()
    remove_workspace.assert_called_once_with("user-1")


@pytest.mark.asyncio
async def test_user_deletion_orchestrates_memory_cleanup(monkeypatch) -> None:
    cleanup = AsyncMock()
    memory_repository = SimpleNamespace(delete_user_data=AsyncMock())
    delete_user = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "noesis.services.user_service.MemorySourceService.delete_derived_user_data", cleanup
    )
    monkeypatch.setattr(
        "noesis.services.user_service.MachineMemoryRepository",
        lambda _db: memory_repository,
    )
    monkeypatch.setattr(
        "noesis.services.user_service.SqlAlchemyUserRepository",
        lambda _db: SimpleNamespace(delete=delete_user),
    )
    db = SimpleNamespace(commit=AsyncMock())

    assert await UserService.delete_user("user-1", db)
    cleanup.assert_awaited_once_with(user_id="user-1")
    memory_repository.delete_user_data.assert_awaited_once_with("user-1")
    delete_user.assert_awaited_once_with("user-1")
    db.commit.assert_awaited_once()
