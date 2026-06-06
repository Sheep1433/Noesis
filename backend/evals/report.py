"""评测报告：per-item scores、aggregate、baseline delta。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from evals.runners.base import DEFAULT_DATASET, EVALS_ROOT, load_dataset

_TARGETS_PATH = EVALS_ROOT / "eval_targets.json"


def load_eval_targets() -> Dict[str, Any]:
    if _TARGETS_PATH.is_file():
        return json.loads(_TARGETS_PATH.read_text(encoding="utf-8"))
    return {
        "point_coverage_recall_min": 0.0,
        "rag_hit_at_3_min": 0.85,
        "dataset_size_min": 20,
    }


def write_item_artifacts(
    run_dir: Path,
    item_id: str,
    run_output: Dict[str, Any],
    scores: Dict[str, Any],
) -> None:
    item_dir = run_dir / "items" / item_id
    item_dir.mkdir(parents=True, exist_ok=True)
    (item_dir / "output.json").write_text(
        json.dumps(run_output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (item_dir / "scores.json").write_text(
        json.dumps(scores, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def compute_item_passed(
    scores: Dict[str, Any],
    ground_truth: Dict[str, Any],
) -> bool:
    """质量门：L0 通过 +（若已评）coverage 达阈值。"""
    l0 = scores.get("l0") or {}
    if not l0.get("passed"):
        return False

    coverage = scores.get("coverage") or {}
    if coverage.get("skipped"):
        return True

    targets = load_eval_targets()
    min_recall = float(targets.get("point_coverage_recall_min", 0.0))
    recall = coverage.get("point_coverage_recall")
    if recall is None or float(recall) < min_recall:
        return False
    return True


def build_aggregate(
    all_scores: List[Dict[str, Any]],
    *,
    dataset_path: Optional[Path] = None,
    scope: str = "full",
) -> Dict[str, Any]:
    n = len(all_scores) or 1
    targets = load_eval_targets()

    l0_pass = sum(1 for s in all_scores if (s.get("l0") or {}).get("passed")) / n

    latencies = [s.get("latency_ms") for s in all_scores if s.get("latency_ms") is not None]
    latency_mean = sum(latencies) / len(latencies) if latencies else 0
    tp_lat = [s.get("latency_ms_testpoints") for s in all_scores if s.get("latency_ms_testpoints") is not None]
    case_lat = [s.get("latency_ms_cases") for s in all_scores if s.get("latency_ms_cases") is not None]

    coverage_scored = [s for s in all_scores if not (s.get("coverage") or {}).get("skipped")]
    coverage_agg: Dict[str, Any] = {}
    if coverage_scored:
        recalls = [
            (s.get("coverage") or {}).get("point_coverage_recall")
            for s in coverage_scored
            if (s.get("coverage") or {}).get("point_coverage_recall") is not None
        ]
        precisions = [
            (s.get("coverage") or {}).get("point_precision")
            for s in coverage_scored
            if (s.get("coverage") or {}).get("point_precision") is not None
        ]
        coverage_agg = {
            "item_count": len(coverage_scored),
            "point_coverage_recall_mean": round(sum(recalls) / len(recalls), 4) if recalls else None,
            "point_precision_mean": round(sum(precisions) / len(precisions), 4) if precisions else None,
        }

    rag_scored = [
        s
        for s in all_scores
        if not (s.get("rag") or {}).get("skipped")
        and (s.get("rag") or {}).get("rag_hit_at_3") is not None
    ]
    rag_agg: Dict[str, Any] = {}
    rag_eval_incomplete = any((s.get("rag") or {}).get("rag_eval_incomplete") for s in all_scores)
    rag_threshold_failed = False
    if rag_scored:
        rag_vals = [(s.get("rag") or {}).get("rag_hit_at_3") for s in rag_scored]
        rag_mean = sum(rag_vals) / len(rag_vals)
        by_ch: Dict[str, List[float]] = {}
        for s in rag_scored:
            for ch, v in ((s.get("rag") or {}).get("rag_hit_at_3_by_channel") or {}).items():
                by_ch.setdefault(ch, []).append(float(v))
        rag_agg = {
            "item_count": len(rag_scored),
            "rag_hit_at_3_mean": round(rag_mean, 4),
            "rag_hit_at_3_by_channel": {
                ch: round(sum(vs) / len(vs), 4) for ch, vs in by_ch.items()
            },
        }
        min_rag = float(targets.get("rag_hit_at_3_min", 0.85))
        rag_threshold_failed = rag_mean < min_rag

    dataset_size_warning = False
    try:
        ds_count = len(load_dataset(dataset_path or DEFAULT_DATASET))
        if ds_count < int(targets.get("dataset_size_min", 20)):
            dataset_size_warning = True
    except (FileNotFoundError, ValueError):
        dataset_size_warning = True

    aggregate: Dict[str, Any] = {
        "item_count": len(all_scores),
        "scope": scope,
        "l0_pass_rate": l0_pass,
        "latency_ms_mean": int(latency_mean),
        "latency_ms_testpoints_mean": int(sum(tp_lat) / len(tp_lat)) if tp_lat else None,
        "latency_ms_cases_mean": int(sum(case_lat) / len(case_lat)) if case_lat else None,
        "dataset_size_warning": dataset_size_warning,
        "rag_eval_incomplete": rag_eval_incomplete,
        "rag_threshold_failed": rag_threshold_failed,
        "quality_gate_passed": all(s.get("passed") for s in all_scores) if all_scores else False,
        "passed_rate": sum(1 for s in all_scores if s.get("passed")) / n,
    }
    if coverage_agg:
        aggregate["coverage"] = coverage_agg
    if rag_agg:
        aggregate["rag"] = rag_agg
    return aggregate


def write_run_summary(
    run_dir: Path,
    *,
    tag: str,
    eval_run_id: str,
    agent_type: str,
    all_scores: List[Dict[str, Any]],
    baseline_dir: Optional[Path] = None,
    dataset_path: Optional[Path] = None,
    scope: str = "full",
) -> Dict[str, Any]:
    aggregate = build_aggregate(all_scores, dataset_path=dataset_path, scope=scope)
    summary: Dict[str, Any] = {
        "eval_run_id": eval_run_id,
        "tag": tag,
        "agent_type": agent_type,
        "scope": scope,
        "aggregate": aggregate,
        "items": all_scores,
    }

    if baseline_dir:
        baseline_path = baseline_dir / "aggregate.json"
        if baseline_path.is_file():
            baseline_agg = json.loads(baseline_path.read_text(encoding="utf-8"))
            summary["baseline_delta"] = _delta(aggregate, baseline_agg)

    (run_dir / "scores.json").write_text(
        json.dumps(all_scores, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "aggregate.json").write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def _delta(current: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for k in ("l0_pass_rate", "passed_rate"):
        c, b = current.get(k), baseline.get(k)
        if isinstance(c, (int, float)) and isinstance(b, (int, float)):
            out[k] = round(float(c) - float(b), 4)

    cur_cov = (current.get("coverage") or {}).get("point_coverage_recall_mean")
    base_cov = (baseline.get("coverage") or {}).get("point_coverage_recall_mean")
    if isinstance(cur_cov, (int, float)) and isinstance(base_cov, (int, float)):
        out["point_coverage_recall_mean"] = round(float(cur_cov) - float(base_cov), 4)

    cur_rag = (current.get("rag") or {}).get("rag_hit_at_3_mean")
    base_rag = (baseline.get("rag") or {}).get("rag_hit_at_3_mean")
    if isinstance(cur_rag, (int, float)) and isinstance(base_rag, (int, float)):
        out["rag_hit_at_3_mean"] = round(float(cur_rag) - float(base_rag), 4)
    return out


def print_summary(summary: Dict[str, Any]) -> None:
    agg = summary.get("aggregate") or {}
    print(f"eval_run_id: {summary.get('eval_run_id')}")
    print(f"tag: {summary.get('tag')}")
    print(f"scope: {summary.get('scope')}")
    print(f"items: {agg.get('item_count')}")
    print(f"L0 pass rate: {agg.get('l0_pass_rate'):.2%}")
    cov = agg.get("coverage") or {}
    if cov.get("point_coverage_recall_mean") is not None:
        print(f"point_coverage_recall_mean: {cov.get('point_coverage_recall_mean')}")
    rag = agg.get("rag") or {}
    if rag.get("rag_hit_at_3_mean") is not None:
        print(f"rag_hit_at_3_mean: {rag.get('rag_hit_at_3_mean')}")
    if agg.get("dataset_size_warning"):
        print("dataset_size_warning: true")
    if agg.get("rag_eval_incomplete"):
        print("rag_eval_incomplete: true")
    if agg.get("rag_threshold_failed"):
        print("rag_threshold_failed: true")
    print(f"quality_gate_passed: {agg.get('quality_gate_passed')}")
    delta = summary.get("baseline_delta")
    if delta:
        print(f"baseline delta: {delta}")
