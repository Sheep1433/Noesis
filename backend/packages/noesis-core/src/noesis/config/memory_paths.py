"""Server-managed paths for rebuildable machine-memory workspace views."""

from __future__ import annotations

import hashlib
from pathlib import Path

from noesis.config.paths import DATA_DIR
from noesis.config.user_data_paths import validate_segment


MEMORY_WORKSPACES_ROOT = DATA_DIR / "memory-workspaces"


def scope_digest(scope_key: str) -> str:
    return hashlib.sha256(scope_key.encode("utf-8")).hexdigest()[:32]


def get_memory_workspace(user_id: str, scope_key: str) -> Path:
    uid = validate_segment(str(user_id), kind="user_id")
    return MEMORY_WORKSPACES_ROOT / uid / scope_digest(scope_key)


def ensure_memory_workspace(user_id: str, scope_key: str) -> Path:
    root = get_memory_workspace(user_id, scope_key)
    (root / "memories").mkdir(parents=True, exist_ok=True)
    (root / "runs").mkdir(parents=True, exist_ok=True)
    return root


__all__ = [
    "MEMORY_WORKSPACES_ROOT",
    "ensure_memory_workspace",
    "get_memory_workspace",
    "scope_digest",
]
