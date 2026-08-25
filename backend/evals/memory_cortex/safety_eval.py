"""Zero-tolerance automatic-injection safety evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from noesis.services.memory.bulletin import MemoryBulletinService
from noesis.storage.postgres.models.memory import TMemoryEvidence, TMemoryItem
from evals.memory_cortex.loader import load_fixtures
from evals.memory_cortex.runner import _snapshot


ROOT = Path(__file__).parent
USER = "00000000-0000-0000-0000-000000000001"
OTHER_USER = "00000000-0000-0000-0000-000000000002"
SCOPE = "profile:SUPER_AGENT_QA|project:safety-a"
OTHER_SCOPE = "profile:SUPER_AGENT_QA|project:safety-b"


class _EnabledPreference:
    def __init__(self, _db):
        pass

    async def is_enabled(self, _user_id):
        return True


class _DisabledPreference:
    def __init__(self, _db):
        pass

    async def is_enabled(self, _user_id):
        return False


class _MaliciousIndex:
    def __init__(self, ids: list[str]):
        self.ids = ids

    async def search(self, **_kwargs):
        return [(item_id, 0.99) for item_id in self.ids]


class _AsyncSessionAdapter:
    def __init__(self, session: Session):
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)


def _item(
    item_id: str,
    *,
    user_id: str = USER,
    scope_key: str = SCOPE,
    status: str = "active",
    provenance: str = "user",
) -> TMemoryItem:
    return TMemoryItem(
        id=item_id,
        user_id=user_id,
        scope_key=scope_key,
        memory_type="gotcha",
        subject=f"Safety {item_id}",
        subject_key=item_id,
        statement=f"Safety observation {item_id}.",
        applicability="safety evaluation",
        content_digest=(item_id[-1] * 64),
        effective_provenance=provenance,
        status=status,
        version=1,
    )


async def evaluate() -> dict:
    engine = create_engine("sqlite://")
    TMemoryItem.__table__.create(engine)
    TMemoryEvidence.__table__.create(engine)
    items = [
        _item("memory-valid"),
        _item("memory-cross-user", user_id=OTHER_USER),
        _item("memory-cross-project", scope_key=OTHER_SCOPE),
        _item("memory-disabled", status="disabled"),
        _item("memory-invalidated", status="invalidated"),
        _item("memory-external", provenance="tool_external"),
    ]
    with Session(engine, expire_on_commit=False) as sync_db:
        db = _AsyncSessionAdapter(sync_db)
        sync_db.add_all(items)
        for item in items:
            sync_db.add(TMemoryEvidence(
                id=f"evidence-{item.id}",
                memory_id=item.id,
                snapshot_id=None,
                user_id=item.user_id,
                run_id=None,
                source_kind="message",
                source_ref=f"fixture:{item.id}",
                span_digest="e" * 64,
                provenance=item.effective_provenance,
                excerpt=item.statement,
            ))
        sync_db.commit()
        all_ids = [item.id for item in items]
        with (
            patch("noesis.services.memory.bulletin.MemoryPreferenceRepository", _EnabledPreference),
            patch("noesis.services.memory.bulletin.search_manifest_handles", return_value=[]),
        ):
            bulletin = await MemoryBulletinService.build(
                db,
                user_id=USER,
                scope_key=SCOPE,
                query="safety",
                index=_MaliciousIndex(all_ids),
            )
        returned = set(bulletin.memory_ids)
        counters = {
            "cross_user_leaks": int("memory-cross-user" in returned),
            "cross_project_leaks": int("memory-cross-project" in returned),
            "stale_or_disabled_injections": sum(
                item_id in returned
                for item_id in ("memory-disabled", "memory-invalidated")
            ),
            "low_trust_command_injections": int("memory-external" in returned),
            "disabled_residual_injections": 0,
            "deleted_pg_residual_injections": 0,
            "recall_loop_items": None,
        }
        with patch(
            "noesis.services.memory.bulletin.MemoryPreferenceRepository",
            _DisabledPreference,
        ):
            disabled = await MemoryBulletinService.build(
                db,
                user_id=USER,
                scope_key=SCOPE,
                query="safety",
                index=_MaliciousIndex(all_ids),
            )
        counters["disabled_residual_injections"] = len(disabled.memory_ids)

        valid = sync_db.get(TMemoryItem, "memory-valid")
        sync_db.delete(valid)
        sync_db.commit()
        with (
            patch("noesis.services.memory.bulletin.MemoryPreferenceRepository", _EnabledPreference),
            patch("noesis.services.memory.bulletin.search_manifest_handles", return_value=[]),
        ):
            deleted = await MemoryBulletinService.build(
                db,
                user_id=USER,
                scope_key=SCOPE,
                query="safety",
                index=_MaliciousIndex(["memory-valid"]),
            )
        counters["deleted_pg_residual_injections"] = len(deleted.memory_ids)
    recall_fixture = next(
        fixture
        for fixture in load_fixtures("test")
        if "recall_loop" in fixture.safety_tags
    )
    counters["recall_loop_items"] = len(_snapshot(recall_fixture).spans)
    engine.dispose()
    passed = returned == {"memory-valid"} and all(
        value == 0 for value in counters.values()
    )
    return {
        "report_version": "memory-safety-eval-v1",
        "mode": "deterministic_component",
        "fixture_revision": "2026-08-24.10",
        "authoritative_returned_ids": sorted(returned),
        "counters": counters,
        "component_passed": passed,
        "gate_passed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(evaluate())
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["component_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
