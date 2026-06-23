"""CLI: uv run python -m evals.agent --tag <name> [--item-id] [--limit] [--compare-to]"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

from evals.agent.dataset import DEFAULT_DATASET, filter_items, load_dataset
from evals.agent.report import build_summary, results_dir_for_tag, write_run_result, write_summary
from evals.agent.runner import run_agent_item
from evals.agent.scoring import score_item
from evals.langfuse_env import eval_langfuse_run

AGENT_ROOT = Path(__file__).resolve().parent


def _resolve_tag(args: argparse.Namespace) -> str:
    return args.tag or os.environ.get("NOESIS_AGENT_EVAL_TAG") or ""


def _resolve_dataset(args: argparse.Namespace) -> Path:
    if args.dataset:
        return args.dataset.resolve()
    env_path = os.environ.get("NOESIS_AGENT_EVAL_DATASET")
    if env_path:
        return Path(env_path).resolve()
    return DEFAULT_DATASET


def run_eval(args: argparse.Namespace) -> int:
    tag = _resolve_tag(args)
    if not tag:
        print("缺少 --tag 或 NOESIS_AGENT_EVAL_TAG", file=sys.stderr)
        return 2

    dataset_path = _resolve_dataset(args)
    dataset_dir = dataset_path.parent
    items = load_dataset(dataset_path)

    item_id = args.item_id or os.environ.get("NOESIS_AGENT_EVAL_ITEM_ID")
    limit = args.limit
    if limit is None:
        env_limit = os.environ.get("NOESIS_AGENT_EVAL_LIMIT")
        if env_limit:
            limit = int(env_limit)

    try:
        items = filter_items(items, item_id=item_id, limit=limit)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    eval_run_id = uuid.uuid4().hex[:16]
    runs = []

    print(f"Agent eval tag={tag} items={len(items)} dataset={dataset_path}")
    for item in items:
        item_id_str = item["id"]
        run_id = uuid.uuid4().hex[:12]
        session_id = f"eval-{item_id_str}-{run_id}"
        print(f"  running {item_id_str} ...", flush=True)
        with eval_langfuse_run(line="agent", tag=tag, session_id=session_id):
            run_output = run_agent_item(
                item,
                dataset_dir=dataset_dir,
                eval_run_id=eval_run_id,
                run_id=run_id,
            )
            workspace_path = Path(run_output["workspace_path"])
            scoring = score_item(
                run_output,
                dict(item.get("ground_truth") or {}),
                workspace_path,
            )
        payload = {**run_output, "scoring": scoring, "ground_truth": item.get("ground_truth")}
        write_run_result(tag, item_id_str, payload)
        runs.append(payload)
        print(
            f"    completed={run_output.get('completed')} "
            f"overall={scoring.get('overall_score')} "
            f"latency_ms={run_output.get('latency_ms')}"
        )

    compare_to = args.compare_to
    if compare_to:
        compare_to = compare_to.resolve()
    summary = build_summary(tag, runs, compare_to=compare_to)
    json_path, md_path = write_summary(tag, summary)
    print(f"Results: {results_dir_for_tag(tag)}")
    print(f"Summary: {json_path}")
    print(f"Report:  {md_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Noesis 深度研究 Agent 离线评测")
    parser.add_argument("--tag", type=str, default=None, help="本次 run 标签（必填）")
    parser.add_argument("--item-id", type=str, default=None, help="仅跑指定 id")
    parser.add_argument("--limit", type=int, default=None, help="仅跑前 N 条")
    parser.add_argument("--dataset", type=Path, default=None, help="dataset.jsonl 路径")
    parser.add_argument(
        "--compare-to",
        type=Path,
        default=None,
        help="与历史 tag 目录或 summary.json 对比",
    )
    args = parser.parse_args(argv)
    return run_eval(args)


if __name__ == "__main__":
    raise SystemExit(main())
