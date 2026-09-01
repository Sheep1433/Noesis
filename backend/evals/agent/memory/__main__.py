"""CLI: uv run python -m evals.agent.memory [--model-id <id>] [--time-budget <s>]。

应召回场景行为评测（agent-memory-cortex：Agentic recall）：含记忆线索的
提问（偏好/决策/经验/目标）→ 断言 Agent 经 search_memory 主动检索。
静默漏召回率 = 1 - recall_rate；需可用 LLM 配置。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.agent.memory.fixtures import EVAL_USER_ID, RECALL_SCENARIOS
from evals.agent.memory.runner import run_memory_recall_sample, seed_eval_memory

ROOT = Path(__file__).resolve().parent


def _run(args: argparse.Namespace) -> int:
    seeded = seed_eval_memory(args.user_id)
    results: list[dict[str, Any]] = []
    for scenario in RECALL_SCENARIOS:
        results.append(
            run_memory_recall_sample(
                scenario,
                user_id=args.user_id,
                time_budget_seconds=args.time_budget,
                model_id=args.model_id or None,
            )
        )
    output = (
        Path(args.output)
        if args.output
        else ROOT / "results" / f"run-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": "noesis-memory-recall-summary/v1",
        "user_id": args.user_id,
        "seeded_entries": seeded,
        "samples": len(results),
        "recall_rate": sum(bool(r["recalled"]) for r in results) / len(results),
        "results": results,
    }
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    console = {
        **{k: v for k, v in summary.items() if k != "results"},
        "results": f"{len(results)} item(s)",
        "output": str(output),
    }
    print(json.dumps(console, ensure_ascii=False, indent=2))
    return 0 if all(r["recalled"] for r in results) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Noesis 记忆召回行为评测（Agentic recall）")
    parser.add_argument("--user-id", default=EVAL_USER_ID)
    parser.add_argument("--model-id", default="")
    parser.add_argument("--time-budget", type=int, default=240)
    parser.add_argument("--output", default="")
    raise SystemExit(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
