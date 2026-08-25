from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from noesis.config import memory_paths
from noesis.schemas.memory import MemorySourceSpan, RunSnapshotPayload
from noesis.services.memory.workspace import MemoryWorkspaceService


@pytest.mark.asyncio
async def test_workspace_is_user_scope_isolated_atomic_and_rebuildable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(memory_paths, "MEMORY_WORKSPACES_ROOT", tmp_path / "memory-workspaces")
    item = SimpleNamespace(
        id="memory-1",
        memory_type="decision",
        status="active",
        subject="Memory switch",
        statement="Use one switch.",
        applicability="Machine memory",
        effective_provenance="user",
        version=1,
        content_digest="a" * 64,
        subject_key="b" * 64,
    )
    payload = RunSnapshotPayload(
        run_id="run-1",
        user_id="user-1",
        session_id="session-1",
        scope_key="profile:SUPER_AGENT_QA|project:global",
        source_watermark=1,
        spans=[MemorySourceSpan(
            id="span-1",
            source_ref="message:user-1",
            kind="user_correction",
            provenance="user",
            effective_provenance="user",
            text="Use one switch.",
            digest="c" * 64,
        )],
        content_digest="d" * 64,
        token_estimate=10,
    )
    snapshot = SimpleNamespace(
        id="snapshot-1",
        evidence_json=payload.model_dump(mode="json"),
        processing_status="succeeded",
        captured_at=datetime.now(timezone.utc),
    )

    class Repository:
        def __init__(self, _db):
            pass

        async def list_scope_items(self, **_kwargs):
            return [item]

        async def list_scope_snapshots(self, **_kwargs):
            return [snapshot]

    monkeypatch.setattr("noesis.services.memory.workspace.MachineMemoryRepository", Repository)
    scope = "profile:SUPER_AGENT_QA|project:global"
    root = await MemoryWorkspaceService.rebuild(
        SimpleNamespace(), user_id="user-1", scope_key=scope
    )
    stale = root / "runs" / "stale.md"
    stale.write_text("stale", encoding="utf-8")

    rebuilt = await MemoryWorkspaceService.rebuild(
        SimpleNamespace(), user_id="user-1", scope_key=scope
    )

    assert rebuilt == root
    assert root.is_relative_to(tmp_path / "memory-workspaces" / "user-1")
    assert not stale.exists()
    assert not list(root.rglob(".*.tmp"))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["items"][0]["id"] == "memory-1"
    assert "scope_key" not in manifest
    assert (root / "memories" / "decisions.md").is_file()
    assert (root / "runs" / "run-1.md").is_file()


def test_user_workspace_cleanup_cannot_escape_managed_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(memory_paths, "MEMORY_WORKSPACES_ROOT", tmp_path / "memory-workspaces")
    root = memory_paths.ensure_memory_workspace("user-1", "scope")
    (root / "manifest.json").write_text("{}", encoding="utf-8")

    MemoryWorkspaceService.remove_user_workspace("user-1")

    assert not root.parent.exists()
    assert tmp_path.exists()
