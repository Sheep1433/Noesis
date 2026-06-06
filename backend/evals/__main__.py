"""CLI: uv run python -m evals --tag <name> [--scope full] [--baseline <path>]"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from evals.report import compute_item_passed, load_eval_targets, print_summary, write_item_artifacts, write_run_summary
from evals.runners.base import (
    DEFAULT_DATASET,
    EvalRunnerError,
    get_runner,
    load_dataset,
    resolve_run_dir,
)
from evals.scorers.coverage import score_coverage
from evals.scorers.l0_structure import score_l0
from evals.scorers.rag_hit import score_rag


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Noesis 测试用例 Agent 离线评测")
    parser.add_argument(
        "--suite",
        "--agent-type",
        dest="agent_type",
        default="test_case",
        help="评测套件（默认 test_case）",
    )
    parser.add_argument("--tag", required=True, help="本次 run 标签，如 baseline / pr-42")
    parser.add_argument(
        "--scope",
        choices=("testpoints", "cases", "full"),
        default="full",
        help="执行范围：testpoints / cases / full",
    )
    parser.add_argument("--dataset", type=Path, default=None, help="dataset.jsonl 路径")
    parser.add_argument("--baseline", type=Path, default=None, help="baseline run 目录")
    parser.add_argument("--limit", type=int, default=None, help="仅跑前 N 条")
    parser.add_argument("--item-id", type=str, default=None, help="仅跑指定 id")
    args = parser.parse_args(argv)

    dataset_path = args.dataset or DEFAULT_DATASET
    dataset_dir = dataset_path.parent
    scope = args.scope

    try:
        runner = get_runner(args.agent_type)
    except EvalRunnerError as e:
        print(str(e), file=sys.stderr)
        return 2

    if scope in ("cases", "full"):
        from services.qdrant_service import init_qdrant_client

        if not asyncio.run(init_qdrant_client()):
            print(
                "Qdrant 连接失败：scope=cases/full 需要向量库。"
                "请确认 Qdrant 已启动且 .env 中 qdrant_host/qdrant_port 正确。",
                file=sys.stderr,
            )
            return 2

    items = load_dataset(dataset_path)
    if args.item_id:
        items = [i for i in items if i["id"] == args.item_id]
        if not items:
            print(f"未找到 item: {args.item_id}", file=sys.stderr)
            return 1
    if args.limit is not None:
        items = items[: args.limit]

    run_dir = resolve_run_dir(args.tag)
    eval_run_id = run_dir.name
    targets = load_eval_targets()

    manifest = {
        "eval_run_id": eval_run_id,
        "tag": args.tag,
        "agent_type": args.agent_type,
        "scope": scope,
        "dataset": str(dataset_path),
        "eval_targets": targets,
        "item_ids": [i["id"] for i in items],
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    all_scores: list = []
    score_coverage_flag = scope in ("testpoints", "full")
    score_rag_flag = scope in ("cases", "full")

    for item in items:
        print(f"Running {item['id']} (scope={scope})...")
        run_output = runner(
            item,
            dataset_dir=dataset_dir,
            eval_run_id=eval_run_id,
            scope=scope,
        )

        gt = item.get("ground_truth") or {}
        l0 = score_l0(run_output)
        coverage = (
            score_coverage(run_output, gt)
            if score_coverage_flag
            else {"skipped": True, "reason": "scope excludes testpoints"}
        )
        rag = (
            score_rag(run_output, gt)
            if score_rag_flag
            else {"skipped": True, "reason": "scope excludes cases"}
        )

        scores = {
            "dataset_item_id": item["id"],
            "scope": scope,
            "latency_ms": run_output.get("latency_ms"),
            "latency_ms_testpoints": run_output.get("latency_ms_testpoints"),
            "latency_ms_cases": run_output.get("latency_ms_cases"),
            "l0": l0,
            "coverage": coverage,
            "rag": rag,
        }
        scores["passed"] = compute_item_passed(scores, gt)
        write_item_artifacts(run_dir, item["id"], run_output, scores)
        all_scores.append(scores)

    summary = write_run_summary(
        run_dir,
        tag=args.tag,
        eval_run_id=eval_run_id,
        agent_type=args.agent_type,
        all_scores=all_scores,
        baseline_dir=args.baseline,
        dataset_path=dataset_path,
        scope=scope,
    )
    print_summary(summary)
    print(f"Results: {run_dir}")

    agg = summary.get("aggregate") or {}
    if agg.get("rag_threshold_failed"):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
