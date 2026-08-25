"""Read safe search handles from the rebuildable workspace manifest."""

from __future__ import annotations

import json

from noesis.config.memory_paths import get_memory_workspace


def search_manifest_handles(
    *, user_id: str, scope_key: str, query: str, limit: int
) -> list[str]:
    path = get_memory_workspace(str(user_id), scope_key) / "manifest.json"
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "memory-workspace-v1" or not isinstance(
        payload.get("items"), list
    ):
        raise ValueError("invalid memory workspace manifest")
    terms = [value.casefold() for value in query.split() if value.strip()]
    scored: list[tuple[int, str]] = []
    for item in payload["items"]:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        text = " ".join(
            str(item.get(key) or "")
            for key in ("subject", "statement", "applicability")
        ).casefold()
        score = sum(term in text for term in terms)
        if score:
            scored.append((score, str(item["id"])))
    return [
        item_id
        for _, item_id in sorted(scored, key=lambda pair: (-pair[0], pair[1]))[:limit]
    ]


__all__ = ["search_manifest_handles"]
