"""Rebuildable, safe file workspace derived only from PostgreSQL state."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.config.memory_paths import ensure_memory_workspace, get_memory_workspace
from noesis.repositories.machine_memory_repository import MachineMemoryRepository
from noesis.schemas.memory import RunSnapshotPayload


_TYPE_FILES = {
    "decision": "decisions.md",
    "experience": "experiences.md",
    "workflow": "workflows.md",
    "gotcha": "gotchas.md",
}


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _memory_markdown(items) -> str:
    lines = ["# Machine memory", ""]
    for item in items:
        lines.extend([
            f"## {item.subject}",
            f"- id: `{item.id}`",
            f"- status: `{item.status}`",
            f"- version: {item.version}",
            f"- applicability: {item.applicability or 'unspecified'}",
            "",
            item.statement,
            "",
        ])
    return "\n".join(lines)


class MemoryWorkspaceService:
    @staticmethod
    async def rebuild(db: AsyncSession, *, user_id: str, scope_key: str) -> Path:
        repository = MachineMemoryRepository(db)
        items = await repository.list_scope_items(user_id=str(user_id), scope_key=scope_key)
        snapshots = await repository.list_scope_snapshots(
            user_id=str(user_id), scope_key=scope_key
        )
        root = ensure_memory_workspace(str(user_id), scope_key)
        expected: set[Path] = set()

        manifest = {
            "schema_version": "memory-workspace-v1",
            "scope_digest": root.name,
            "items": [
                {
                    "id": item.id,
                    "type": item.memory_type,
                    "status": item.status,
                    "subject": item.subject,
                    "statement": item.statement,
                    "applicability": item.applicability,
                    "effective_provenance": item.effective_provenance,
                    "version": item.version,
                    "content_digest": item.content_digest,
                }
                for item in items
            ],
        }
        manifest_path = root / "manifest.json"
        _atomic_write(
            manifest_path,
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )
        expected.add(manifest_path)

        active = [item for item in items if item.status == "active"]
        summary_path = root / "memory_summary.md"
        _atomic_write(summary_path, _memory_markdown(active))
        expected.add(summary_path)
        for memory_type, filename in _TYPE_FILES.items():
            path = root / "memories" / filename
            _atomic_write(path, _memory_markdown([
                item for item in items if item.memory_type == memory_type
            ]))
            expected.add(path)

        for snapshot in snapshots:
            if not isinstance(snapshot.evidence_json, dict):
                continue
            payload = RunSnapshotPayload.model_validate(snapshot.evidence_json)
            path = root / "runs" / f"{payload.run_id}.md"
            lines = [f"# Run {payload.run_id}", "", f"status: {snapshot.processing_status}", ""]
            for span in payload.spans:
                lines.extend([
                    f"## {span.kind} · {span.id}",
                    span.text[:600],
                    "",
                ])
            _atomic_write(path, "\n".join(lines))
            expected.add(path)

        for directory in (root / "memories", root / "runs"):
            for path in directory.glob("*"):
                if path.is_file() and path not in expected:
                    path.unlink()
        return root

    @staticmethod
    def remove_user_workspace(user_id: str) -> None:
        user_root = get_memory_workspace(str(user_id), "placeholder").parent
        if not user_root.is_dir():
            return
        for path in sorted(user_root.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        user_root.rmdir()


__all__ = ["MemoryWorkspaceService"]
