"""压缩评测结果汇总：headline = recall% @ retained tokens，逐 fixture 对照 uncompacted 算 Δ。"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from evals.compression.rubric import DIMENSIONS

COMPRESSION_ROOT = Path(__file__).resolve().parent
RESULTS_ROOT = COMPRESSION_ROOT / "results"

UNCOMPACTED = "uncompacted"


def results_dir_for_tag(tag: str) -> Path:
    return RESULTS_ROOT / tag.replace("/", "_")


def write_arm_run(tag: str, fixture_id: str, arm: str, payload: Dict[str, Any]) -> Path:
    out_dir = results_dir_for_tag(tag) / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{fixture_id}.{arm}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def _run_recall_pct(run: Dict[str, Any]) -> Optional[float]:
    """单次 run 的 recall%：Σrecall / (2×有效题数)；解析失败题剔除出分母。"""
    valid = [p for p in run["probes"] if p.get("recall") is not None]
    if not valid:
        return None
    return sum(int(p["recall"]) for p in valid) / (2 * len(valid))


def _run_parse_error_rate(run: Dict[str, Any]) -> float:
    probes = run.get("probes") or []
    if not probes:
        return 0.0
    return sum(1 for p in probes if p.get("recall") is None) / len(probes)


def summarize_arm_runs(arm_runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """同一 fixture 同一 arm 的多次 run → 单行摘要（中位数聚合）。"""
    if not arm_runs:
        return {}
    first = arm_runs[0]
    recalls = [r for r in (_run_recall_pct(run) for run in arm_runs) if r is not None]
    parse_errors = [_run_parse_error_rate(run) for run in arm_runs]

    per_probe: Dict[str, Dict[str, List[float]]] = {}
    for run in arm_runs:
        for probe in run["probes"]:
            pid = probe["probe_id"]
            for dim in DIMENSIONS:
                per_probe.setdefault(pid, {d: [] for d in DIMENSIONS})[dim].append(
                    float(probe["scores"].get(dim, 0)))
    dim_medians = {
        d: statistics.median(
            statistics.median(per_probe[pid][d]) for pid in per_probe
        ) if per_probe else 0.0
        for d in DIMENSIONS
    }

    compression = first.get("compression") or {}
    return {
        "fixture_id": first["fixture_id"],
        "arm": first["arm"],
        "policy": first.get("policy"),
        "runs": len(arm_runs),
        "recall_pct": round(statistics.median(recalls), 4) if recalls else None,
        "judge_parse_error_rate": round(statistics.median(parse_errors), 4),
        "retained_tokens": compression.get("post_tokens"),
        "pre_tokens": compression.get("pre_tokens"),
        "compression_ratio": compression.get("compression_ratio"),
        "dimension_medians": {d: round(dim_medians[d], 4) for d in DIMENSIONS},
        "probes": first["probes"],
    }


def attach_retention_deltas(arm_summaries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """逐 fixture：压缩臂 recall% − uncompacted 臂 recall% = 任务保持率 Δ。"""
    by_fixture: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for row in arm_summaries:
        by_fixture.setdefault(row["fixture_id"], {})[row["arm"]] = row
    for arms in by_fixture.values():
        base = arms.get(UNCOMPACTED)
        if not base or base.get("recall_pct") is None:
            continue
        for arm, row in arms.items():
            if arm != UNCOMPACTED and row.get("recall_pct") is not None:
                row["retention_delta"] = round(
                    row["recall_pct"] - base["recall_pct"], 4)
    return arm_summaries


def build_summary(
    tag: str,
    arm_summaries: List[Dict[str, Any]],
    *,
    runs_per_arm: int = 1,
    compare_to: Optional[Path] = None,
) -> Dict[str, Any]:
    arm_summaries = attach_retention_deltas(arm_summaries)

    overall: Dict[str, Dict[str, Any]] = {}
    for row in arm_summaries:
        slot = overall.setdefault(row["arm"], {"recall_pcts": [], "retained": []})
        if row.get("recall_pct") is not None:
            slot["recall_pcts"].append(row["recall_pct"])
        if row.get("retained_tokens") is not None:
            slot["retained"].append(row["retained_tokens"])

    baseline: Dict[str, Any] = {}
    if compare_to:
        baseline_path = compare_to / "summary.json" if compare_to.is_dir() else compare_to
        if baseline_path.is_file():
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    return {
        "tag": tag,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runs_per_arm": runs_per_arm,
        "fixture_count": len({r["fixture_id"] for r in arm_summaries}),
        "compare_to": str(compare_to) if compare_to else None,
        "arms": {
            arm: {
                "recall_pct": round(statistics.median(v["recall_pcts"]), 4) if v["recall_pcts"] else None,
                "retained_tokens_median": round(statistics.median(v["retained"]), 1) if v["retained"] else None,
            }
            for arm, v in overall.items()
        },
        "baseline": baseline.get("arms") if baseline else None,
        "fixtures": arm_summaries,
    }


def write_summary(tag: str, summary: Dict[str, Any]) -> tuple[Path, Path]:
    out_dir = results_dir_for_tag(tag)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "summary.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    arms = summary.get("arms") or {}
    lines = [
        f"# Compression Eval Summary: {tag}",
        "",
        f"- generated_at: {summary.get('generated_at')}",
        f"- runs_per_arm: {summary.get('runs_per_arm')} / fixtures: {summary.get('fixture_count')}",
        "",
        "## headline：recall% @ retained tokens",
        "",
        "| arm | recall% | retained tokens（中位） |",
        "|---|---:|---:|",
    ]
    for arm, v in arms.items():
        recall = f"{v['recall_pct']:.1%}" if v.get("recall_pct") is not None else "-"
        retained = v.get("retained_tokens_median")
        lines.append(f"| {arm} | {recall} | {retained if retained is not None else '-'} |")

    lines.extend([
        "",
        "## 逐 fixture（Δ = 压缩臂 recall% − uncompacted recall%）",
        "",
        "| fixture | arm | recall% | retained | Δ | judge 解析失败率 |",
        "|---|---|---:|---:|---:|---:|",
    ])
    for row in summary.get("fixtures") or []:
        recall = f"{row['recall_pct']:.1%}" if row.get("recall_pct") is not None else "-"
        delta = row.get("retention_delta")
        delta_str = f"{delta:+.1%}" if delta is not None else "-"
        retained = row.get("retained_tokens")
        lines.append(
            f"| {row['fixture_id']} | {row['arm']} | {recall} | "
            f"{retained if retained is not None else '-'} | {delta_str} |"
            f" {row.get('judge_parse_error_rate', 0):.1%} |"
        )

    lines.extend(["", "## 诊断维度（五维中位，不进 headline）", "",
                  "| fixture | arm | " + " | ".join(DIMENSIONS) + " |",
                  "|---|---|" + "---:|" * len(DIMENSIONS)])
    for row in summary.get("fixtures") or []:
        dims = row.get("dimension_medians") or {}
        lines.append(
            f"| {row['fixture_id']} | {row['arm']} | "
            + " | ".join(f"{float(dims.get(d) or 0):.1f}" for d in DIMENSIONS) + " |"
        )

    md_path = out_dir / "summary.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
