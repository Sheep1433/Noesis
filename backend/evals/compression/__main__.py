"""CLI: uv run python -m evals.compression --tag <name> [--fixture] [--runs] [--compare-to]"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

from evals.compression.driver import compress_fixture_messages, parse_fixture_messages
from evals.compression.fixture_loader import filter_fixtures, list_fixture_ids, load_fixture, load_probes
from evals.compression.grader import grade_single_probe
from evals.langfuse_env import eval_langfuse_run
from domain.observability.langfuse import eval_langfuse_observation
from evals.compression.report import (
    build_summary,
    results_dir_for_tag,
    summarize_fixture_runs,
    write_fixture_run,
    write_summary,
)


def _resolve_tag(args: argparse.Namespace) -> str:
    return args.tag or os.environ.get("NOESIS_COMPRESSION_EVAL_TAG") or ""


def _resolve_runs(args: argparse.Namespace) -> int:
    if args.runs is not None:
        return max(1, int(args.runs))
    env_runs = os.environ.get("NOESIS_COMPRESSION_EVAL_RUNS")
    return max(1, int(env_runs)) if env_runs else 1


def run_fixture_once(fixture_id: str, eval_run_id: str, tag: str) -> dict:
    fixture = load_fixture(fixture_id)
    probes_doc = load_probes(fixture_id)
    session_id = f"eval-compression-{fixture_id}-{eval_run_id}"

    with eval_langfuse_run(line="compression", tag=tag, session_id=session_id):
        with eval_langfuse_observation(
            name=f"compression/{fixture_id}",
            input_data={"fixture_id": fixture_id, "eval_run_id": eval_run_id},
        ):
            messages = parse_fixture_messages(fixture["messages"])
            compression = compress_fixture_messages(
                messages,
                compress_options=dict(fixture.get("compress_options") or {}),
            )
            compressed_messages = compression["compressed_messages"]

            probe_results = [
                grade_single_probe(compressed_messages, probe) for probe in probes_doc["probes"]
            ]
    return {
        "fixture_id": fixture_id,
        "eval_run_id": eval_run_id,
        "description": fixture.get("description"),
        "compression": {
            k: compression[k]
            for k in (
                "compressed",
                "pre_tokens",
                "post_tokens",
                "compression_ratio",
                "pre_message_count",
                "post_message_count",
                "summary_text",
            )
        },
        "probes": probe_results,
    }


def run_eval(args: argparse.Namespace) -> int:
    tag = _resolve_tag(args)
    if not tag:
        print("缺少 --tag 或 NOESIS_COMPRESSION_EVAL_TAG", file=sys.stderr)
        return 2

    fixture_filter = args.fixture or os.environ.get("NOESIS_COMPRESSION_EVAL_FIXTURE")
    runs = _resolve_runs(args)

    try:
        fixture_ids = filter_fixtures(list_fixture_ids(), fixture=fixture_filter)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    eval_run_id = uuid.uuid4().hex[:16]
    all_summaries = []

    print(f"Compression eval tag={tag} fixtures={len(fixture_ids)} runs={runs}")
    for fixture_id in fixture_ids:
        run_payloads = []
        for run_idx in range(runs):
            print(f"  {fixture_id} run {run_idx + 1}/{runs} ...", flush=True)
            payload = run_fixture_once(fixture_id, eval_run_id, tag)
            payload["run_index"] = run_idx
            run_payloads.append(payload)

        summary = summarize_fixture_runs(run_payloads)
        write_fixture_run(tag, fixture_id, summary)
        all_summaries.append(summary)
        comp = summary.get("compression") or {}
        print(
            f"    fixture_score={summary.get('fixture_score')} "
            f"compression_ratio={comp.get('compression_ratio')}"
        )

    compare_to = args.compare_to.resolve() if args.compare_to else None
    full_summary = build_summary(tag, all_summaries, compare_to=compare_to, runs_per_fixture=runs)
    json_path, md_path = write_summary(tag, full_summary)
    print(f"Results: {results_dir_for_tag(tag)}")
    print(f"Summary: {json_path}")
    print(f"Report:  {md_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Noesis 消息压缩离线评测")
    parser.add_argument("--tag", type=str, default=None, help="本次 run 标签（必填）")
    parser.add_argument("--fixture", type=str, default=None, help="仅跑指定 fixture id")
    parser.add_argument("--runs", type=int, default=None, help="同一 fixture 重复次数（取中位数）")
    parser.add_argument(
        "--compare-to",
        type=Path,
        default=None,
        help="与历史 results/<tag> 目录或 summary.json 对比",
    )
    args = parser.parse_args(argv)
    return run_eval(args)


if __name__ == "__main__":
    raise SystemExit(main())
