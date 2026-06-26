"""Agent 性能回归集：uv run python -m evals.agent.perf --tag <name>"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

from evals.agent.perf.dataset import DEFAULT_DATASET, filter_items, load_dataset
from evals.agent.perf.report import build_summary, results_dir_for_tag, write_run_result, write_summary
from evals.agent.perf.runner import run_agent_item
from evals.agent.perf.scoring import score_item
from evals.langfuse_env import eval_langfuse_run


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Agent 性能回归评测（自研题集，非官方 benchmark）")
    p.add_argument("--tag", required=True)
    p.add_argument("--item-id", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dataset", type=Path, default=None)
    p.add_argument("--compare-to", type=Path, default=None)
    args = p.parse_args(argv)

    dataset_path = args.dataset.resolve() if args.dataset else DEFAULT_DATASET
    if not args.dataset and os.environ.get("NOESIS_AGENT_EVAL_DATASET"):
        dataset_path = Path(os.environ["NOESIS_AGENT_EVAL_DATASET"]).resolve()

    items = load_dataset(dataset_path)
    item_id = args.item_id or os.environ.get("NOESIS_AGENT_EVAL_ITEM_ID")
    limit = args.limit
    if limit is None and os.environ.get("NOESIS_AGENT_EVAL_LIMIT"):
        limit = int(os.environ["NOESIS_AGENT_EVAL_LIMIT"])
    try:
        items = filter_items(items, item_id=item_id, limit=limit)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2

    eval_run_id = uuid.uuid4().hex[:16]
    runs = []
    for item in items:
        iid = item["id"]
        rid = uuid.uuid4().hex[:12]
        with eval_langfuse_run(line="agent", tag=args.tag, session_id=f"eval-{iid}-{rid}"):
            out = run_agent_item(item, dataset_dir=dataset_path.parent, eval_run_id=eval_run_id, run_id=rid)
            ws = Path(out["workspace_path"])
            scoring = score_item(out, dict(item.get("ground_truth") or {}), ws)
        payload = {**out, "scoring": scoring}
        write_run_result(args.tag, iid, payload)
        runs.append(payload)
        print(f"{iid}: overall={scoring.get('overall_score')} completed={out.get('completed')}")

    j, m = write_summary(args.tag, build_summary(args.tag, runs, compare_to=args.compare_to.resolve() if args.compare_to else None))
    print(f"Results: {results_dir_for_tag(args.tag)}\nSummary: {j}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
