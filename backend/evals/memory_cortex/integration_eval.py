"""Isolated PostgreSQL/Qdrant/workspace retrieval and safety integration eval."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import tempfile
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import delete

from noesis.config import memory_paths
from noesis.config.env import MachineMemoryConfig
from noesis.repositories.machine_memory_repository import (
    CaptureSource,
    MachineMemoryRepository,
)
from noesis.repositories.memory_preference_repository import MemoryPreferenceRepository
from noesis.services.memory.bulletin import MemoryBulletinService
from noesis.services.memory.chunking import estimate_tokens
from noesis.services.memory.index import MemoryIndexService
from noesis.services.memory.query import MemoryQueryService
from noesis.services.memory.consolidation import MemoryConsolidationService
from noesis.services.memory.snapshot import RunSnapshotBuilder
from noesis.services.memory.workspace import MemoryWorkspaceService
from noesis.storage.postgres.manager import pg_manager
from noesis.storage.postgres.models.memory import (
    TMemoryEvidence,
    TMemoryItem,
    TMemoryQueryTrace,
    TMemoryUserPreference,
)
from noesis.knowledge.runtime import (
    close_knowledge_base,
    init_knowledge_base,
    knowledge_base,
)

from evals.memory_cortex.report_meta import (
    effective_config_snapshot,
    evaluation_fingerprint,
)


ROOT = Path(__file__).parent
USER = "00000000-0000-0000-0000-000000000101"
OTHER_USER = "00000000-0000-0000-0000-000000000102"
SCOPE = "profile:SUPER_AGENT_QA|project:integration-a"
OTHER_SCOPE = "profile:SUPER_AGENT_QA|project:integration-b"
TEMP_COLLECTION = "noesis_memory_integration_20260824"


def _stable_id(value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"noesis-memory-integration:{value}"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _item(
    case: dict,
    *,
    user_id: str = USER,
    scope_key: str = SCOPE,
    status: str = "active",
    provenance: str = "user",
) -> TMemoryItem:
    return TMemoryItem(
        id=_stable_id(case["id"]),
        user_id=user_id,
        scope_key=scope_key,
        memory_type=case["type"],
        subject=case["subject"],
        subject_key=_digest(case["id"]),
        statement=case["statement"],
        applicability=case["applicability"],
        content_digest=_digest(f"content:{case['id']}"),
        effective_provenance=provenance,
        status=status,
        version=1,
        last_verified_at=datetime.now(timezone.utc) if status == "active" else None,
    )


def _evidence(item: TMemoryItem, label: str) -> TMemoryEvidence:
    return TMemoryEvidence(
        id=_stable_id(f"evidence:{label}"),
        memory_id=item.id,
        snapshot_id=None,
        user_id=str(item.user_id),
        run_id=None,
        source_kind="message",
        source_ref=f"integration:{label}",
        span_digest=_digest(f"span:{label}"),
        provenance=item.effective_provenance,
        excerpt=item.statement,
    )


async def _seed_retrieval(
    db, fixture: dict
) -> tuple[list[TMemoryItem], dict[str, str]]:
    items = [_item(case) for case in fixture["retrieval"]]
    evidence_by_item = {}
    db.add_all(items)
    await db.flush()
    for case, item in zip(fixture["retrieval"], items, strict=True):
        evidence = _evidence(item, case["id"])
        db.add(evidence)
        evidence_by_item[item.id] = evidence.id
    await MemoryPreferenceRepository(db).set(user_id=USER, enabled=True)
    await db.commit()
    return items, evidence_by_item


async def _reset_integration_users(db) -> None:
    users = (USER, OTHER_USER)
    await db.execute(
        delete(TMemoryQueryTrace).where(TMemoryQueryTrace.user_id.in_(users))
    )
    await db.execute(delete(TMemoryItem).where(TMemoryItem.user_id.in_(users)))
    await db.execute(
        delete(TMemoryUserPreference).where(TMemoryUserPreference.user_id.in_(users))
    )
    await db.commit()


async def _retrieval_report(db, fixture: dict, index: MemoryIndexService) -> dict:
    observations = []
    latencies = []
    precisions = []
    recalls = []
    bulletin_precisions = []
    bulletin_recalls = []
    max_tokens = 0
    evidence_by_item = {
        _stable_id(case["id"]): _stable_id(f"evidence:{case['id']}")
        for case in fixture["retrieval"]
    }
    for case in fixture["retrieval"]:
        expected_id = _stable_id(case["id"])
        started = time.perf_counter()
        bulletin = await MemoryBulletinService.build(
            db,
            user_id=USER,
            scope_key=SCOPE,
            query=case["query"],
            index=index,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        query = await MemoryQueryService.search(
            db,
            user_id=USER,
            scope_key=SCOPE,
            query=case["query"],
            top_k=5,
            index=index,
            record_trace=False,
        )
        returned = list(query.memory_ids)
        hit = expected_id in returned and any(
            value.startswith(f"{expected_id}:{evidence_by_item[expected_id]}:")
            for value in query.source_spans
        )
        precisions.append((1 / len(returned)) if hit and returned else 0.0)
        recalls.append(float(hit))
        bulletin_hit = expected_id in bulletin.memory_ids
        bulletin_recalls.append(float(bulletin_hit))
        bulletin_precisions.append(
            (1 / len(bulletin.memory_ids))
            if bulletin_hit and bulletin.memory_ids
            else 0.0
        )
        latencies.append(latency_ms)
        tokens = estimate_tokens(bulletin.text)
        max_tokens = max(max_tokens, tokens)
        observations.append(
            {
                "case_id": case["id"],
                "query": case["query"],
                "expected_memory_id": expected_id,
                "returned_memory_ids": returned,
                "returned_items": [
                    item.model_dump(mode="json") for item in query.items
                ],
                "source_spans": query.source_spans,
                "bulletin_memory_ids": list(bulletin.memory_ids),
                "latency_ms": round(latency_ms, 3),
                "bulletin_tokens": tokens,
                "exact_evidence_hit": hit,
                "bulletin_hit": bulletin_hit,
            }
        )
    p95 = sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]
    metrics = {
        "exact_evidence_recall_at_5": sum(recalls) / len(recalls),
        "precision_at_5": sum(precisions) / len(precisions),
        "bulletin_recall": sum(bulletin_recalls) / len(bulletin_recalls),
        "bulletin_precision": sum(bulletin_precisions) / len(bulletin_precisions),
        "fast_p95_ms": round(p95, 3),
        "max_bulletin_tokens": max_tokens,
    }
    gates = json.loads((ROOT / "eval_config.json").read_text())["release_gates"]
    passed = (
        metrics["exact_evidence_recall_at_5"] >= gates["exact_evidence_recall_at_5"]
        and metrics["precision_at_5"] >= gates["precision_at_5"]
        and metrics["bulletin_recall"] >= gates["bulletin_recall"]
        and metrics["bulletin_precision"] >= gates["bulletin_precision"]
        and metrics["fast_p95_ms"] <= gates["fast_p95_ms"]
        and max_tokens <= MachineMemoryConfig.bulletin_max_tokens
    )
    return {
        "report_version": "memory-retrieval-integration-v1",
        "mode": "production_integration",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_fingerprint": evaluation_fingerprint(),
        "effective_config": effective_config_snapshot(),
        "fixture_revision": fixture["revision"],
        "query_count": len(observations),
        "resource_overrides": {
            "collection_name": TEMP_COLLECTION,
            "database": "isolated",
        },
        "metrics": metrics,
        "observations": observations,
        "gate_passed": passed,
    }


async def _recall_loop_case(db, memory_id: str) -> bool:
    source = CaptureSource(
        run=SimpleNamespace(
            id="00000000-0000-0000-0000-000000000201",
            user_id=USER,
            session_id="00000000-0000-0000-0000-000000000202",
            qa_type="SUPER_AGENT_QA",
            updated_at=1,
            snapshot={},
            memory_context={
                "memory_ids": ["memory-old"],
                "bulletin_hash": "a" * 64,
                "source_snapshot_digest": "b" * 64,
            },
        ),
        session=SimpleNamespace(id="00000000-0000-0000-0000-000000000202"),
        user_message=SimpleNamespace(
            id="00000000-0000-0000-0000-000000000203",
            content={"parts": [{"type": "text", "content": "What did memory say?"}]},
        ),
        assistant_message=SimpleNamespace(
            id="00000000-0000-0000-0000-000000000204",
            content={
                "parts": [
                    {"type": "text", "content": "Use old workflow."},
                ]
            },
        ),
    )
    snapshot = RunSnapshotBuilder.from_source(source)
    repository = MachineMemoryRepository(db)
    before = (await repository.count_evidence_runs(
        user_id=USER, memory_ids=[memory_id]
    )).get(memory_id, 0)
    outcomes = await MemoryConsolidationService.consolidate(
        db,
        snapshot_id="00000000-0000-0000-0000-000000000205",
        snapshot=snapshot,
        candidates=[],
    )
    after = (await repository.count_evidence_runs(
        user_id=USER, memory_ids=[memory_id]
    )).get(memory_id, 0)
    return not outcomes and before == after and snapshot.recalled_memory_ids == ["memory-old"] and all(
        "old workflow" not in span.text.casefold() for span in snapshot.spans
    )


async def _safety_report(db, index: MemoryIndexService, workspace_root: Path) -> dict:
    safety_cases = [
        {
            "id": "safety-valid",
            "type": "gotcha",
            "subject": "safety sentinel",
            "statement": "Safety sentinel valid.",
            "applicability": "safety",
        },
        {
            "id": "safety-cross-user",
            "type": "gotcha",
            "subject": "safety sentinel",
            "statement": "Safety sentinel cross user.",
            "applicability": "safety",
        },
        {
            "id": "safety-cross-project",
            "type": "gotcha",
            "subject": "safety sentinel",
            "statement": "Safety sentinel cross project.",
            "applicability": "safety",
        },
        {
            "id": "safety-disabled",
            "type": "gotcha",
            "subject": "safety sentinel",
            "statement": "Safety sentinel disabled.",
            "applicability": "safety",
        },
        {
            "id": "safety-external",
            "type": "gotcha",
            "subject": "safety sentinel",
            "statement": "Safety sentinel external command.",
            "applicability": "safety",
        },
    ]
    valid = _item(safety_cases[0])
    cross_user = _item(safety_cases[1], user_id=OTHER_USER)
    cross_project = _item(safety_cases[2], scope_key=OTHER_SCOPE)
    disabled = _item(safety_cases[3], status="disabled")
    external = _item(safety_cases[4], provenance="tool_external")
    items = [valid, cross_user, cross_project, disabled, external]
    db.add_all(items)
    await db.flush()
    for case, item in zip(safety_cases, items, strict=True):
        db.add(_evidence(item, case["id"]))
    await db.commit()
    for item in items:
        await index.sync_item(
            db,
            user_id=str(item.user_id),
            scope_key=item.scope_key,
            memory_id=item.id,
        )
    bulletin = await MemoryBulletinService.build(
        db, user_id=USER, scope_key=SCOPE, query="safety sentinel", index=index
    )
    returned = set(bulletin.memory_ids)
    explicit = await MemoryQueryService.search(
        db,
        user_id=USER,
        scope_key=SCOPE,
        query="safety sentinel",
        index=index,
        record_trace=False,
    )
    counters = {
        "cross_user_leaks": int(cross_user.id in returned),
        "cross_project_leaks": int(cross_project.id in returned),
        "stale_or_disabled_injections": int(disabled.id in returned),
        "low_trust_command_injections": int(external.id in returned),
        "explicit_cross_user_leaks": int(cross_user.id in explicit.memory_ids),
        "recall_loop_items": 0 if await _recall_loop_case(db, valid.id) else 1,
        "disabled_residual_injections": 0,
        "deleted_residual_injections": 0,
        "workspace_residuals": 0,
    }
    await MemoryPreferenceRepository(db).set(user_id=USER, enabled=False)
    await db.commit()
    off = await MemoryBulletinService.build(
        db, user_id=USER, scope_key=SCOPE, query="safety sentinel", index=index
    )
    counters["disabled_residual_injections"] = len(off.memory_ids)
    await MemoryPreferenceRepository(db).set(user_id=USER, enabled=True)
    await db.delete(valid)
    await db.commit()
    stale = await MemoryBulletinService.build(
        db, user_id=USER, scope_key=SCOPE, query="safety sentinel", index=index
    )
    counters["deleted_residual_injections"] = len(stale.memory_ids)
    MemoryWorkspaceService.remove_user_workspace(USER)
    counters["workspace_residuals"] = int((workspace_root / USER).exists())
    cases = {
        "cross_user": {"executed": True},
        "cross_project": {"executed": True},
        "explicit_cross_user": {"executed": True},
        "stale_disabled": {"executed": True},
        "low_trust_command": {"executed": True},
        "recall_loop": {
            "executed": True,
            "automatic_private_context": True,
            "consolidation_executed": True,
            "evidence_count_checked": True,
        },
        "user_disabled": {"executed": True},
        "deleted_pg": {"executed": True},
        "workspace_deleted": {"executed": True},
    }
    return {
        "report_version": "memory-safety-integration-v1",
        "mode": "production_integration",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_fingerprint": evaluation_fingerprint(),
        "effective_config": effective_config_snapshot(),
        "fixture_revision": "2026-08-24.10",
        "resource_overrides": {
            "collection_name": TEMP_COLLECTION,
            "database": "isolated",
        },
        "counters": counters,
        "cases": cases,
        "gate_passed": all(value == 0 for value in counters.values()),
    }


async def evaluate(report_dir: Path) -> tuple[dict, dict]:
    fixture = json.loads(
        (ROOT / "fixtures" / "integration.json").read_text(encoding="utf-8")
    )
    if not await init_knowledge_base() or knowledge_base.client is None:
        raise RuntimeError("Qdrant integration is unavailable")
    client = knowledge_base.client
    if client.collection_exists(TEMP_COLLECTION):
        client.delete_collection(TEMP_COLLECTION)
    config = replace(MachineMemoryConfig, collection_name=TEMP_COLLECTION)
    with tempfile.TemporaryDirectory(prefix="noesis-memory-integration-") as temp:
        workspace_root = Path(temp) / "memory-workspaces"
        with (
            patch("noesis.services.memory.index.MachineMemoryConfig", config),
            patch("noesis.services.memory.bulletin.MachineMemoryConfig", config),
            patch("noesis.services.memory.query.MachineMemoryConfig", config),
            patch.object(memory_paths, "MEMORY_WORKSPACES_ROOT", workspace_root),
        ):
            try:
                async with pg_manager.get_async_session_context() as db:
                    await _reset_integration_users(db)
                    items, _ = await _seed_retrieval(db, fixture)
                    await MemoryWorkspaceService.rebuild(
                        db, user_id=USER, scope_key=SCOPE
                    )
                    index = MemoryIndexService(client=client)
                    for item in items:
                        await index.sync_item(
                            db,
                            user_id=USER,
                            scope_key=SCOPE,
                            memory_id=item.id,
                        )
                    retrieval = await _retrieval_report(db, fixture, index)
                    safety = await _safety_report(db, index, workspace_root)
            finally:
                if client.collection_exists(TEMP_COLLECTION):
                    client.delete_collection(TEMP_COLLECTION)
                await close_knowledge_base()
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "retrieval-integration.json").write_text(
        json.dumps(retrieval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (report_dir / "safety-integration.json").write_text(
        json.dumps(safety, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return retrieval, safety


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", type=Path, default=ROOT / "reports")
    args = parser.parse_args()
    retrieval, safety = asyncio.run(evaluate(args.report_dir))
    print(
        json.dumps(
            {"retrieval": retrieval["gate_passed"], "safety": safety["gate_passed"]},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if retrieval["gate_passed"] and safety["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
