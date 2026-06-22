"""压缩评测结果汇总与 baseline 对比。"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from evals.compression.rubric import DIMENSIONS

COMPRESSION_ROOT = Path(__file__).resolve().parent
RESULTS_ROOT = COMPRESSION_ROOT / "results"


def results_dir_for_tag(tag: str) -> Path:
    return RESULTS_ROOT / tag.replace("/", "_")


def write_fixture_run(tag: str, fixture_id: str, payload: Dict[str, Any]) -> Path:
    out_dir = results_dir_for_tag(tag) / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{fixture_id}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def _median(values: List[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def summarize_fixture_runs(fixture_runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not fixture_runs:
        return {}

    fixture_id = fixture_runs[0]["fixture_id"]
    probe_ids = [p["probe_id"] for p in fixture_runs[0]["probes"]]
    per_probe_dim: Dict[str, Dict[str, List[float]]] = {
        pid: {d: [] for d in DIMENSIONS} for pid in probe_ids
    }
    per_probe_overall: Dict[str, List[float]] = {pid: [] for pid in probe_ids}

    for run in fixture_runs:
        for probe in run["probes"]:
            pid = probe["probe_id"]
            for dim in DIMENSIONS:
                per_probe_dim[pid][dim].append(float(probe["scores"].get(dim, 0)))
            per_probe_overall[pid].append(float(probe.get("overall_probe_score") or 0))

    dim_medians = {
        d: _median([_median(per_probe_dim[pid][d]) for pid in probe_ids]) for d in DIMENSIONS
    }
    probe_medians = [_median(per_probe_overall[pid]) for pid in probe_ids]
    fixture_score = _median(probe_medians)

    return {
        "fixture_id": fixture_id,
        "runs": len(fixture_runs),
        "fixture_score": round(fixture_score, 4),
        "dimension_medians": {d: round(dim_medians[d], 4) for d in DIMENSIONS},
        "compression": fixture_runs[0].get("compression", {}),
        "probes": fixture_runs[0]["probes"],
    }


def load_baseline_summaries(compare_to: Path) -> List[Dict[str, Any]]:
    base_dir = compare_to
    if base_dir.is_file():
        data = json.loads(base_dir.read_text(encoding="utf-8"))
        return list(data.get("fixtures") or [])
    summary_path = base_dir / "summary.json"
    if summary_path.is_file():
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        return list(data.get("fixtures") or [])
    runs_dir = base_dir / "runs"
    if not runs_dir.is_dir():
        return []
    by_fixture: Dict[str, List[Dict[str, Any]]] = {}
    for path in sorted(runs_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        by_fixture.setdefault(payload["fixture_id"], []).append(payload)
    return [summarize_fixture_runs(runs) for runs in by_fixture.values()]


def build_summary(
    tag: str,
    fixture_summaries: List[Dict[str, Any]],
    *,
    compare_to: Optional[Path] = None,
    runs_per_fixture: int = 1,
) -> Dict[str, Any]:
    baseline = load_baseline_summaries(compare_to) if compare_to else []
    baseline_by_id = {b["fixture_id"]: b for b in baseline}

    fixtures_out: List[Dict[str, Any]] = []
    for summary in fixture_summaries:
        fid = summary["fixture_id"]
        row = dict(summary)
        if fid in baseline_by_id:
            base = baseline_by_id[fid]
            row["delta_fixture_score"] = round(
                summary["fixture_score"] - float(base.get("fixture_score") or 0), 4
            )
            row["delta_dimensions"] = {
                d: round(
                    summary["dimension_medians"][d] - float(base.get("dimension_medians", {}).get(d) or 0),
                    4,
                )
                for d in DIMENSIONS
            }
        fixtures_out.append(row)

    scores = [float(s["fixture_score"]) for s in fixture_summaries]
    return {
        "tag": tag,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runs_per_fixture": runs_per_fixture,
        "fixture_count": len(fixture_summaries),
        "avg_fixture_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
        "compare_to": str(compare_to) if compare_to else None,
        "fixtures": fixtures_out,
    }


def write_summary(tag: str, summary: Dict[str, Any]) -> tuple[Path, Path]:
    out_dir = results_dir_for_tag(tag)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "summary.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# Compression Eval Summary: {tag}",
        "",
        f"- generated_at: {summary.get('generated_at')}",
        f"- runs_per_fixture: {summary.get('runs_per_fixture')}",
        f"- avg_fixture_score: {summary.get('avg_fixture_score')}",
    ]
    if summary.get("compare_to"):
        lines.append(f"- compare_to: {summary.get('compare_to')}")
    lines.extend(
        [
            "",
            "| fixture | score | accuracy | artifact | context | continuity | completeness | delta |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in summary.get("fixtures") or []:
        dims = row.get("dimension_medians") or {}
        delta = row.get("delta_fixture_score")
        delta_str = f"{delta:+.4f}" if delta is not None else "-"
        lines.append(
            "| {fid} | {score:.4f} | {acc:.2f} | {art:.2f} | {ctx:.2f} | {cont:.2f} | {comp:.2f} | {delta} |".format(
                fid=row.get("fixture_id"),
                score=float(row.get("fixture_score") or 0),
                acc=float(dims.get("accuracy") or 0),
                art=float(dims.get("artifact_trail") or 0),
                ctx=float(dims.get("context_awareness") or 0),
                cont=float(dims.get("continuity") or 0),
                comp=float(dims.get("completeness") or 0),
                delta=delta_str,
            )
        )

    md_path = out_dir / "summary.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
