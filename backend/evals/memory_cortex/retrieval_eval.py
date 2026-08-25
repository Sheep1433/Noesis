"""Deterministic fast-retrieval and Bulletin evaluation using production ranking code."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from evals.memory_cortex.metrics import retrieval_metrics
from noesis.services.memory.bulletin import MemoryBulletinService, lexical_score
from noesis.services.memory.chunking import estimate_tokens


ROOT = Path(__file__).parent
USER_ID = "00000000-0000-0000-0000-000000000001"
SCOPE = "profile:SUPER_AGENT_QA|project:retrieval-fixture"


def _item(item_id: str, memory_type: str, subject: str, statement: str, applicability: str):
    return SimpleNamespace(
        id=item_id,
        user_id=USER_ID,
        scope_key=SCOPE,
        memory_type=memory_type,
        subject=subject,
        statement=statement,
        applicability=applicability,
        status="active",
        valid_to=None,
    )


CORPUS = [
    _item("memory-switch", "decision", "memory enablement", "Use one user-controlled memory switch.", "memory settings"),
    _item("stale-lock", "experience", "stale dependency lock", "Regenerate a stale dependency lock and verify the build.", "dependency resolution failure"),
    _item("diagnose-first", "workflow", "diagnosis workflow", "Reproduce the failure before changing code; stop when it cannot reproduce.", "bug diagnosis"),
    _item("workspace-boundary", "gotcha", "workspace boundary", "Writes outside the authorized workspace fail.", "file operations"),
]

PROBES = [
    ("How is memory enablement controlled?", "memory-switch"),
    ("How should a stale dependency lock be repaired?", "stale-lock"),
    ("What is the diagnosis workflow before code changes?", "diagnose-first"),
    ("Why did a write outside the workspace fail?", "workspace-boundary"),
]


def _semantic_terms(value: str) -> set[str]:
    terms: set[str] = set()
    for token in re.findall(r"[a-z0-9]+", value.casefold()):
        if len(token) < 3:
            continue
        terms.add(token)
        if len(token) > 4 and token.endswith("s"):
            terms.add(token[:-1])
        if len(token) > 5 and token.endswith("ing"):
            terms.add(token[:-3])
    return terms


def _semantic_score(query: str, item) -> float:
    wanted = _semantic_terms(query)
    actual = _semantic_terms(
        f"{item.subject} {item.statement} {item.applicability}"
    )
    return len(wanted & actual) / max(1, len(wanted))


class _Preference:
    def __init__(self, _db):
        pass

    async def is_enabled(self, _user_id):
        return True


class _Repository:
    def __init__(self, _db):
        pass

    async def lexical_candidates(self, *, user_id, scope_key, query, limit):
        assert user_id == USER_ID and scope_key == SCOPE
        return sorted(CORPUS, key=lambda item: -lexical_score(query, item))[:limit]

    async def eligible_items_by_ids(self, *, user_id, scope_key, memory_ids):
        assert user_id == USER_ID and scope_key == SCOPE
        wanted = set(memory_ids)
        return [item for item in CORPUS if item.id in wanted]


class _Index:
    async def search(self, *, query, user_id, scope_key, limit):
        assert user_id == USER_ID and scope_key == SCOPE
        scored = sorted(
            ((item.id, _semantic_score(query, item)) for item in CORPUS),
            key=lambda pair: (-pair[1], pair[0]),
        )
        return scored[:limit]


async def evaluate() -> dict:
    returned: list[list[str]] = []
    relevant: list[set[str]] = []
    latencies: list[float] = []
    bulletin_tokens: list[int] = []
    observations: list[dict[str, object]] = []
    with (
        patch("noesis.services.memory.bulletin.MemoryPreferenceRepository", _Preference),
        patch("noesis.services.memory.bulletin.MachineMemoryRepository", _Repository),
        patch("noesis.services.memory.bulletin.search_manifest_handles", return_value=[]),
    ):
        for query, relevant_id in PROBES:
            started = time.perf_counter()
            bulletin = await MemoryBulletinService.build(
                SimpleNamespace(),
                user_id=USER_ID,
                scope_key=SCOPE,
                query=query,
                index=_Index(),
            )
            latency = (time.perf_counter() - started) * 1000
            tokens = estimate_tokens(bulletin.text)
            returned.append(list(bulletin.memory_ids))
            relevant.append({relevant_id})
            latencies.append(latency)
            bulletin_tokens.append(tokens)
            observations.append({
                "query": query,
                "relevant_id": relevant_id,
                "returned_ids": list(bulletin.memory_ids),
                "latency_ms": round(latency, 3),
                "bulletin_tokens": tokens,
            })
    metrics = retrieval_metrics(
        relevant=relevant,
        returned=returned,
        expected_abstain=[False] * len(PROBES),
        k=5,
    )
    p95 = sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]
    config = json.loads((ROOT / "eval_config.json").read_text(encoding="utf-8"))
    gates = config["release_gates"]
    passed = (
        metrics.recall_at_k >= gates["exact_evidence_recall_at_5"]
        and metrics.precision_at_k >= gates["precision_at_5"]
        and p95 <= gates["fast_p95_ms"]
        and max(bulletin_tokens, default=0) <= config["bulletin_max_tokens"]
    )
    return {
        "report_version": "memory-retrieval-eval-v1",
        "mode": "deterministic_component",
        "fixture_revision": "2026-08-24.10",
        "metrics": {
            "item_recall_at_5": metrics.recall_at_k,
            "item_precision_at_5": metrics.precision_at_k,
            "abstention_accuracy": metrics.abstention_accuracy,
            "fast_p95_ms": round(p95, 3),
            "max_bulletin_tokens": max(bulletin_tokens, default=0),
        },
        "observations": observations,
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
