from __future__ import annotations

import json

import pytest

from noesis.config import memory_paths
from noesis.services.memory.manifest import search_manifest_handles


def test_manifest_search_returns_only_stable_handles(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(memory_paths, "MEMORY_WORKSPACES_ROOT", tmp_path)
    root = memory_paths.ensure_memory_workspace("user-1", "scope")
    (root / "manifest.json").write_text(json.dumps({
        "schema_version": "memory-workspace-v1",
        "items": [
            {"id": "memory-b", "subject": "cache layout", "statement": "late context", "applicability": "runtime"},
            {"id": "memory-a", "subject": "other", "statement": "unrelated", "applicability": "none"},
        ],
    }), encoding="utf-8")

    assert search_manifest_handles(
        user_id="user-1", scope_key="scope", query="cache", limit=5
    ) == ["memory-b"]


def test_invalid_manifest_fails_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(memory_paths, "MEMORY_WORKSPACES_ROOT", tmp_path)
    root = memory_paths.ensure_memory_workspace("user-1", "scope")
    (root / "manifest.json").write_text('{"items": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        search_manifest_handles(
            user_id="user-1", scope_key="scope", query="cache", limit=5
        )
