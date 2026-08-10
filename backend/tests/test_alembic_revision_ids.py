from __future__ import annotations

import ast
from pathlib import Path


def test_alembic_revision_ids_are_unique() -> None:
    versions = Path(__file__).parents[1] / "alembic" / "versions"
    seen: dict[str, Path] = {}
    for path in sorted(versions.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        revision = None
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if any(isinstance(target, ast.Name) and target.id == "revision" for target in node.targets):
                revision = ast.literal_eval(node.value)
                break
        if revision is None:
            for node in tree.body:
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "revision":
                    revision = ast.literal_eval(node.value)
                    break
        assert revision, f"migration missing revision: {path.name}"
        assert revision not in seen, (
            f"duplicate Alembic revision {revision}: {seen.get(revision)} and {path}"
        )
        seen[revision] = path
