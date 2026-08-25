"""Deterministic consolidation-operation evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from evals.memory_cortex.metrics import operation_accuracy
from evals.memory_cortex.report_meta import (
    effective_config_snapshot,
    evaluation_fingerprint,
)
from noesis.schemas.memory import (
    MemorySourceSpan,
    RunSnapshotPayload,
    ValidatedMemoryCandidate,
)
from noesis.services.memory.consolidation import decide_operation
from noesis.storage.postgres.models.memory import TMemoryItem


ROOT = Path(__file__).parent


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def evaluate() -> dict:
    fixture = json.loads(
        (ROOT / "fixtures" / "operations.json").read_text(encoding="utf-8")
    )
    expected: list[str] = []
    observed: list[str] = []
    cases: list[dict[str, str]] = []
    for case in fixture["cases"]:
        span = MemorySourceSpan(
            id="span-1",
            source_ref="fixture:span-1",
            kind=case["span_kind"],
            provenance="user" if case["span_kind"] == "user_correction" else "tool_internal",
            effective_provenance=(
                "user" if case["span_kind"] == "user_correction" else "tool_internal"
            ),
            text="Validated memory decision.",
            digest=_digest("span"),
        )
        snapshot = RunSnapshotPayload(
            run_id="operation-fixture-run",
            user_id="00000000-0000-0000-0000-000000000001",
            session_id="operation-fixture-session",
            scope_key="profile:SUPER_AGENT_QA|project:fixture",
            source_watermark=1,
            spans=[span],
            content_digest=_digest(case["id"]),
            token_estimate=10,
        )
        candidate = ValidatedMemoryCandidate(
            memory_type="decision",
            subject="Memory switch",
            subject_key=_digest("memory-switch"),
            statement=case.get("candidate_statement", "Use one user switch."),
            evidence_refs=["span-1"],
            effective_provenance=span.effective_provenance,
            confidence_reason="Fixture evidence supports the candidate.",
            proposed_relation=case["relation"],
            content_digest=case["candidate_digest"] * 64,
            chunk_ids=[_digest("chunk")],
        )
        current_data = case["current"]
        current = None
        if current_data:
            current = TMemoryItem(
                id=f"current-{case['id']}",
                user_id=snapshot.user_id,
                scope_key=snapshot.scope_key,
                memory_type="decision",
                subject="Memory switch",
                subject_key=candidate.subject_key,
                statement=current_data["statement"],
                applicability="",
                content_digest=current_data["digest"] * 64,
                effective_provenance="user",
                status=current_data["status"],
                version=1,
                user_revision=current_data["user_revision"],
            )
        actual = decide_operation(current, candidate, snapshot)
        expected.append(case["expected"])
        observed.append(actual)
        cases.append({"id": case["id"], "expected": case["expected"], "observed": actual})
    accuracy = operation_accuracy(expected, observed)
    return {
        "report_version": "memory-consolidation-eval-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_fingerprint": evaluation_fingerprint(),
        "effective_config": effective_config_snapshot(),
        "mode": "production_deterministic",
        "fixture_schema_version": fixture["schema_version"],
        "fixture_revision": fixture["revision"],
        "operation_accuracy": accuracy,
        "cases": cases,
        "gate_passed": accuracy >= 0.85,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate()
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
