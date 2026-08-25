"""Reproducible structural and optional live extraction evaluation."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from evals.memory_cortex.contracts import get_pipeline
from evals.memory_cortex.loader import load_fixtures
from evals.memory_cortex.metrics import capture_metrics, extraction_metrics
from evals.memory_cortex.schema import CandidateObservation, FixtureObservation
from evals.memory_cortex.report_meta import (
    effective_config_snapshot,
    evaluation_fingerprint,
)
from noesis.schemas.memory import MemorySourceSpan, RunSnapshotPayload
from noesis.services.memory.chunking import MemoryChunker
from noesis.services.memory.extractor import MemoryExtractor
from noesis.services.memory.model import StructuredCandidateModel
from noesis.config.env import MachineMemoryConfig, ModelConfig


ROOT = Path(__file__).parent
_RESUMABLE_PROVIDER_FAILURES = frozenset({
    "APIConnectionError",
    "APIError",
    "APITimeoutError",
    "InternalServerError",
    "RateLimitError",
    "TimeoutError",
    "timeout",
})


def _config() -> dict:
    return json.loads((ROOT / "eval_config.json").read_text(encoding="utf-8"))


def _snapshot(fixture) -> RunSnapshotPayload:
    memory_recall_ids = {
        span.id for span in fixture.run.evidence if span.provenance == "memory_recall"
    }
    spans: list[MemorySourceSpan] = []
    for span in fixture.run.evidence:
        if span.provenance in {"system", "memory_recall"}:
            continue
        if set(span.derived_from) & memory_recall_ids:
            continue
        provenance = span.provenance.value
        effective = (
            "tool_external"
            if provenance == "assistant_derived"
            and any(
                source.id in span.derived_from and source.provenance == "tool_external"
                for source in fixture.run.evidence
            )
            else provenance
        )
        digest = hashlib.sha256(span.text.encode("utf-8")).hexdigest()
        spans.append(MemorySourceSpan(
            id=span.id,
            source_ref=f"fixture:{fixture.id}:{span.id}",
            kind=span.kind.value,
            provenance=provenance,
            effective_provenance=effective,
            text=span.text,
            digest=digest,
            derived_from=span.derived_from,
        ))
    material = json.dumps(
        [span.model_dump(mode="json") for span in spans],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return RunSnapshotPayload(
        run_id=fixture.id[:36],
        user_id="eval-user",
        session_id=f"eval-{fixture.split}",
        scope_key=f"profile:{fixture.run.agent_profile}|project:{fixture.run.project_key}",
        source_watermark=1,
        spans=spans,
        content_digest=hashlib.sha256(material.encode("utf-8")).hexdigest(),
        token_estimate=(len(material) + 3) // 4,
    )


def structural_report() -> dict:
    fixtures = load_fixtures()
    pipeline = get_pipeline()
    expected = [fixture.expected_capture for fixture in fixtures]
    observed = [pipeline.observe(fixture).captured for fixture in fixtures]
    metrics = capture_metrics(expected, observed)
    return {
        "fixtures": len(fixtures),
        "capture": metrics.__dict__,
    }


async def live_extraction_report(
    split: str,
    model_id: str | None = None,
    *,
    completed_observations: dict[str, FixtureObservation] | None = None,
) -> dict:
    fixtures = [fixture for fixture in load_fixtures(split) if fixture.expected_capture]
    config = _config()
    chunker = MemoryChunker(max_tokens=1600)
    extractor = MemoryExtractor(
        StructuredCandidateModel(model_id=model_id, seed=config["seed"]),
        concurrency=config["live_chunk_concurrency"],
        chunk_attempts=2,
        retry_delay_seconds=config["live_chunk_retry_delay_seconds"],
    )
    observations: list[FixtureObservation] = []
    started = time.perf_counter()
    for fixture in fixtures:
        if completed_observations and fixture.id in completed_observations:
            observations.append(completed_observations[fixture.id])
            continue
        snapshot = _snapshot(fixture)
        chunks = chunker.chunk(snapshot)
        try:
            result = await asyncio.wait_for(
                extractor.extract(snapshot, chunks),
                timeout=config["live_fixture_timeout_seconds"],
            )
        except TimeoutError:
            observations.append(FixtureObservation(
                fixture_id=fixture.id,
                captured=True,
                expected_chunk_ids=[chunk.chunk_id for chunk in chunks],
                failed_chunk_ids=[chunk.chunk_id for chunk in chunks],
                failure_category="timeout",
            ))
            continue
        observations.append(FixtureObservation(
            fixture_id=fixture.id,
            captured=True,
            expected_chunk_ids=[chunk.chunk_id for chunk in chunks],
            processed_chunk_ids=list(result.processed_chunk_ids),
            failed_chunk_ids=list(result.failed_chunk_ids),
            failure_category=(
                ",".join(sorted({category for _, category in result.failed_chunk_categories}))
                or None
            ),
            candidates=[
                CandidateObservation(
                    memory_type=item.memory_type,
                    subject=item.subject,
                    statement=item.statement,
                    applicability=item.applicability,
                    evidence_refs=item.evidence_refs,
                )
                for item in result.candidates
            ],
        ))
    metrics = extraction_metrics(fixtures, observations)
    gates = config["release_gates"]
    passed = (
        metrics.precision >= gates["extraction_precision"]
        and metrics.recall >= gates["extraction_recall"]
        and metrics.source_span_precision >= gates["source_span_precision"]
        and metrics.source_span_recall >= gates["source_span_recall"]
    )
    return {
        "split": split,
        "fixtures": len(fixtures),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "runtime_model": model_id or MachineMemoryConfig.extraction_model or ModelConfig.model_name,
        "metrics": metrics.__dict__,
        "observations": [item.model_dump(mode="json") for item in observations],
        "passed": passed,
    }


def _runtime_model_name(model_id: str | None) -> str:
    return model_id or MachineMemoryConfig.extraction_model or ModelConfig.model_name


def _load_resume_state(
    path: Path, *, split: str, model_id: str | None = None
) -> tuple[dict, dict[str, FixtureObservation]]:
    source = json.loads(path.read_text(encoding="utf-8"))
    live = source.get("live_extraction") or {}
    if source.get("mode") != "live" or live.get("split") != split:
        raise ValueError("resume source must be an incomplete live report for this split")
    if live.get("passed"):
        raise ValueError("a passed live report cannot be resumed")
    if source.get("evaluation_fingerprint") != evaluation_fingerprint():
        raise ValueError("resume source code fingerprint is stale")
    if source.get("effective_config") != effective_config_snapshot():
        raise ValueError("resume source effective config is stale")
    if source.get("config") != _config():
        raise ValueError("resume source eval config is stale")
    if live.get("runtime_model") != _runtime_model_name(model_id):
        raise ValueError("resume source runtime model is different")
    expected_ids = {
        fixture.id for fixture in load_fixtures(split) if fixture.expected_capture
    }
    observations = [
        FixtureObservation.model_validate(item)
        for item in live.get("observations") or []
    ]
    if {item.fixture_id for item in observations} != expected_ids:
        raise ValueError("resume observations do not match the frozen split")
    failed_observations = [item for item in observations if item.failed_chunk_ids]
    if not failed_observations:
        raise ValueError("resume source has no failed fixture")
    for item in failed_observations:
        categories = set((item.failure_category or "").split(","))
        if not categories or not categories <= _RESUMABLE_PROVIDER_FAILURES:
            raise ValueError("resume source contains a non-provider failure")
    completed = {
        item.fixture_id: item
        for item in observations
        if not item.failed_chunk_ids and item.failure_category is None
    }
    return source, completed


def _resume_history(source: dict) -> list[dict]:
    previous = list(source.get("resume_history") or [])
    failed = [
        item
        for item in (source.get("live_extraction") or {}).get("observations", [])
        if item.get("failed_chunk_ids")
    ]
    return [
        *previous,
        {
            "attempt": int(source.get("resume_attempt") or 0),
            "attempted_at": source.get("resumed_at") or source.get("created_at"),
            "failed_observations": failed,
        },
    ]


async def run(
    *,
    live: bool,
    split: str = "dev",
    model_id: str | None = None,
    resume_from: Path | None = None,
) -> dict:
    config = _config()
    resume_source = None
    completed_observations = None
    if resume_from is not None:
        if not live:
            raise ValueError("resume requires live mode")
        resume_source, completed_observations = _load_resume_state(
            resume_from, split=split, model_id=model_id
        )
    report = {
        "report_version": "memory-eval-report-v1",
        "mode": "live" if live else "structural",
        "created_at": (
            resume_source["created_at"]
            if resume_source is not None
            else datetime.now(timezone.utc).isoformat()
        ),
        "evaluation_fingerprint": evaluation_fingerprint(),
        "effective_config": effective_config_snapshot(),
        "config": config,
        "structural": structural_report(),
        "live_extraction": None,
        "release_ready": False,
    }
    if live:
        if os.getenv("NOESIS_MEMORY_LIVE_EVAL") != "1":
            raise RuntimeError("set NOESIS_MEMORY_LIVE_EVAL=1 to run live extraction eval")
        report["live_extraction"] = await live_extraction_report(
            split,
            model_id,
            completed_observations=completed_observations,
        )
        if resume_source is not None:
            report["continued_incomplete_run"] = True
            report["resume_attempt"] = int(resume_source.get("resume_attempt") or 0) + 1
            report["resumed_at"] = datetime.now(timezone.utc).isoformat()
            report["resume_history"] = _resume_history(resume_source)
    report["release_ready"] = False
    report["remaining_release_gates"] = [
        "test_split_extraction",
        "consolidation_operations",
        "retrieval_and_bulletin",
        "safety_zero_tolerance",
        "paired_memory_on_off",
        "cache_scenarios",
    ]
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--model-id")
    parser.add_argument("--resume-from", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(
        run(
            live=args.live,
            split=args.split,
            model_id=args.model_id,
            resume_from=args.resume_from,
        )
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if report["structural"]["capture"]["silent_drop_rate"] != 0:
        return 1
    if args.live and not report["live_extraction"]["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
