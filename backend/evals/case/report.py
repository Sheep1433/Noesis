"""promptfoo 评测结果汇总：解析 JSON、打印控制台指标、写入 summary.json。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

CASE_ROOT = Path(__file__).resolve().parent

# 控制台优先展示的指标顺序（其余 metric 按字母序追加）
METRIC_ORDER = {
    "testpoints": ("l0", "point_coverage_recall", "point_coverage_precision"),
    "rag": (
        "historical_requirements_recall_at_3",
        "historical_requirements_hit_at_3",
        "historical_test_cases_recall_at_3",
        "historical_test_cases_hit_at_3",
        "document_context_present",
    ),
}


def results_dir_for_tag(tag: str) -> Path:
    return CASE_ROOT / "results" / tag


def default_eval_json_path(tag: str, phase: str) -> Path:
    return results_dir_for_tag(tag) / f"{phase}.json"


def default_summary_json_path(tag: str, phase: str) -> Path:
    return results_dir_for_tag(tag) / f"{phase}-summary.json"


def load_promptfoo_eval(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _item_id(row: Dict[str, Any]) -> str:
    test_case = row.get("testCase") or {}
    vars_ = row.get("vars") or test_case.get("vars") or {}
    metadata = test_case.get("metadata") or {}
    return str(vars_.get("item_id") or metadata.get("item_id") or test_case.get("description") or "unknown")


def _row_named_scores(row: Dict[str, Any]) -> Dict[str, float]:
    ns = row.get("namedScores")
    if not isinstance(ns, dict):
        ns = (row.get("gradingResult") or {}).get("namedScores") or {}
    out: Dict[str, float] = {}
    for key, value in ns.items():
        if isinstance(value, (int, float)):
            out[str(key)] = float(value)
    return out


def summarize_promptfoo_eval(data: Dict[str, Any], *, tag: str, phase: str) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = (data.get("results") or {}).get("results") or []
    metric_sums: Dict[str, float] = {}
    metric_counts: Dict[str, int] = {}
    per_item: List[Dict[str, Any]] = []

    pass_count = 0
    for row in rows:
        if row.get("success"):
            pass_count += 1
        scores = _row_named_scores(row)
        item = {"item_id": _item_id(row), "success": bool(row.get("success")), "metrics": scores}
        per_item.append(item)
        for metric, score in scores.items():
            metric_sums[metric] = metric_sums.get(metric, 0.0) + score
            metric_counts[metric] = metric_counts.get(metric, 0) + 1

    dataset_size = len(rows)
    metrics_summary: Dict[str, Any] = {}
    for metric, total in metric_sums.items():
        count = metric_counts[metric]
        mean = round(total / count, 4) if count else 0.0
        entry: Dict[str, Any] = {"mean": mean, "count": count}
        if metric == "l0":
            pass_n = sum(1 for item in per_item if item["metrics"].get("l0", 0) >= 1.0)
            entry["pass_count"] = pass_n
            entry["pass_rate"] = round(pass_n / dataset_size, 4) if dataset_size else 0.0
        metrics_summary[metric] = entry

    worst_recall: List[Dict[str, Any]] = []
    if "point_coverage_recall" in metric_sums:
        ranked = sorted(
            (
                {
                    "item_id": item["item_id"],
                    "point_coverage_recall": item["metrics"].get("point_coverage_recall", 0.0),
                    "point_coverage_precision": item["metrics"].get("point_coverage_precision"),
                }
                for item in per_item
            ),
            key=lambda x: x["point_coverage_recall"],
        )
        worst_recall = [row for row in ranked if row["point_coverage_recall"] < 1.0][:3]

    return {
        "tag": tag,
        "phase": phase,
        "eval_id": data.get("evalId"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_size": dataset_size,
        "pass_count": pass_count,
        "pass_rate": round(pass_count / dataset_size, 4) if dataset_size else 0.0,
        "metrics": metrics_summary,
        "worst_recall": worst_recall,
        "items": per_item,
    }


def _ordered_metric_names(metrics: Dict[str, Any], phase: str) -> List[str]:
    preferred = METRIC_ORDER.get(phase, ())
    names = list(metrics.keys())
    ordered = [m for m in preferred if m in names]
    ordered.extend(sorted(m for m in names if m not in ordered))
    return ordered


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def format_case_summary_lines(summary: Dict[str, Any], *, eval_json: Optional[Path] = None) -> List[str]:
    phase = summary.get("phase") or ""
    tag = summary.get("tag") or ""
    dataset_size = int(summary.get("dataset_size") or 0)
    pass_count = int(summary.get("pass_count") or 0)
    pass_rate = float(summary.get("pass_rate") or 0.0)
    metrics: Dict[str, Any] = summary.get("metrics") or {}

    lines = [
        "",
        f"--- Eval summary (tag={tag}, phase={phase}) ---",
        f"Pass rate: {pass_count}/{dataset_size} ({_fmt_pct(pass_rate)})",
    ]

    for metric in _ordered_metric_names(metrics, phase):
        entry = metrics[metric]
        mean = float(entry.get("mean") or 0.0)
        if metric == "l0" and "pass_count" in entry:
            l0_pass = int(entry["pass_count"])
            lines.append(f"{metric + '_pass_rate':<28} {_fmt_pct(l0_pass / dataset_size) if dataset_size else '0.0%':>7} ({l0_pass}/{dataset_size})")
        else:
            lines.append(f"{metric + '_mean':<28} {_fmt_pct(mean):>7}")

    worst = summary.get("worst_recall") or []
    if worst:
        parts = []
        for row in worst:
            recall = float(row.get("point_coverage_recall") or 0.0)
            parts.append(f"{row.get('item_id')} ({recall:.2f})")
        lines.append(f"Lowest recall: {', '.join(parts)}")

    eval_id = summary.get("eval_id")
    if eval_id:
        lines.append(f"View: cd backend/evals/case/{phase} && npx promptfoo@latest view  (eval id: {eval_id})")
    if eval_json is not None:
        lines.append(f"Results JSON: {eval_json}")
    lines.append("")
    return lines


def print_case_summary(summary: Dict[str, Any], *, eval_json: Optional[Path] = None) -> None:
    for line in format_case_summary_lines(summary, eval_json=eval_json):
        print(line)


def write_case_summary(summary: Dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
