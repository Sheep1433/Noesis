"""Agent 评测结果写入与汇总。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

AGENT_ROOT = Path(__file__).resolve().parent
RESULTS_ROOT = AGENT_ROOT / "results"


def results_dir_for_tag(tag: str) -> Path:
    safe = tag.replace("/", "_")
    return RESULTS_ROOT / safe


def write_run_result(tag: str, item_id: str, payload: Dict[str, Any]) -> Path:
    out_dir = results_dir_for_tag(tag) / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{item_id}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_run_result(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_baseline_summary(compare_to: Path) -> Optional[Dict[str, Any]]:
    candidate = compare_to
    if candidate.is_dir():
        candidate = candidate / "summary.json"
    if not candidate.is_file():
        return None
    return json.loads(candidate.read_text(encoding="utf-8"))


def build_summary(
    tag: str,
    runs: List[Dict[str, Any]],
    *,
    compare_to: Optional[Path] = None,
) -> Dict[str, Any]:
    scores = [float(r.get("scoring", {}).get("overall_score") or 0.0) for r in runs]
    avg_score = round(sum(scores) / len(scores), 4) if scores else 0.0

    baseline = load_baseline_summary(compare_to) if compare_to else None
    baseline_by_id: Dict[str, float] = {}
    if baseline:
        for row in baseline.get("items") or []:
            iid = row.get("dataset_item_id")
            if iid:
                baseline_by_id[str(iid)] = float(row.get("overall_score") or 0.0)

    items_summary: List[Dict[str, Any]] = []
    for run in runs:
        scoring = run.get("scoring") or {}
        item_id = run.get("dataset_item_id")
        overall = float(scoring.get("overall_score") or 0.0)
        row: Dict[str, Any] = {
            "dataset_item_id": item_id,
            "overall_score": overall,
            "rule_score": scoring.get("rule_score"),
            "semantic_score": scoring.get("semantic_score"),
            "completed": run.get("completed"),
            "latency_ms": run.get("latency_ms"),
        }
        if item_id and item_id in baseline_by_id:
            row["delta_vs_baseline"] = round(overall - baseline_by_id[item_id], 4)
        items_summary.append(row)

    return {
        "tag": tag,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "item_count": len(runs),
        "avg_overall_score": avg_score,
        "compare_to": str(compare_to) if compare_to else None,
        "items": items_summary,
    }


def write_summary(tag: str, summary: Dict[str, Any]) -> tuple[Path, Path]:
    out_dir = results_dir_for_tag(tag)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "summary.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# Agent Eval Summary: {tag}",
        "",
        f"- generated_at: {summary.get('generated_at')}",
        f"- item_count: {summary.get('item_count')}",
        f"- avg_overall_score: {summary.get('avg_overall_score')}",
    ]
    if summary.get("compare_to"):
        lines.append(f"- compare_to: {summary.get('compare_to')}")
    lines.extend(["", "| item_id | overall | rule | semantic | completed | latency_ms | delta |", "|---|---:|---:|---:|---|---:|---:|"])

    for row in summary.get("items") or []:
        delta = row.get("delta_vs_baseline")
        delta_str = f"{delta:+.4f}" if delta is not None else "-"
        lines.append(
            "| {id} | {overall:.4f} | {rule} | {semantic} | {completed} | {latency} | {delta} |".format(
                id=row.get("dataset_item_id"),
                overall=float(row.get("overall_score") or 0.0),
                rule=row.get("rule_score"),
                semantic=row.get("semantic_score"),
                completed=row.get("completed"),
                latency=row.get("latency_ms"),
                delta=delta_str,
            )
        )

    md_path = out_dir / "summary.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
