"""CLI: uv run python -m evals.agent.rag --dataset <jsonl>."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.agent.rag.runner import run_agentic_rag_sample
from evals.bootstrap import agentic_rag_runtime

ROOT = Path(__file__).resolve().parent


def load_dataset(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not str(row.get("query") or "").strip():
            raise ValueError(f"dataset line {lineno} missing query")
        rows.append(row)
    if not rows:
        raise ValueError("Agentic RAG dataset is empty")
    return rows


async def _run(args: argparse.Namespace) -> int:
    dataset = Path(args.dataset)
    if not dataset.is_absolute():
        dataset = ROOT / dataset
    rows = load_dataset(dataset)
    results: list[dict[str, Any]] = []
    async with agentic_rag_runtime():
        for row in rows:
            results.append(
                await run_agentic_rag_sample(
                    row,
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
        "schema_version": "noesis-agentic-rag/v1",
        "dataset": str(dataset),
        "samples": len(results),
        "kb_tool_call_rate": sum(bool(r["kb_tool_called"]) for r in results) / len(results),
        "mean_source_recall": sum(float(r["source_score"]["source_recall"]) for r in results) / len(results),
        "results": results,
    }
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    console = {
        **summary,
        "results": f"{len(results)} item(s)",
        "output": str(output),
    }
    print(json.dumps(console, ensure_ascii=False, indent=2))
    return 0 if all(r["completed"] and r["kb_tool_called"] for r in results) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Noesis core Agentic RAG evaluation")
    parser.add_argument("--dataset", default="fixtures/sample.jsonl")
    parser.add_argument("--output", default="")
    parser.add_argument("--model-id", default="")
    parser.add_argument("--time-budget", type=int, default=180)
    raise SystemExit(asyncio.run(_run(parser.parse_args())))


if __name__ == "__main__":
    main()
