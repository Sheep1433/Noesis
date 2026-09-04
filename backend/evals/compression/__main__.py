"""CLI: 压缩评测（多臂对照：uncompacted 上限 + 压缩策略矩阵）。

用法（backend/ 下）:
    uv run python -m evals.compression --tag t1 \
        --model-id <作答模型> --judge-model-id <判卷模型> [--arms uncompacted,current]
    uv run python -m evals.compression --tag t1 --fixture debug_session --runs 3 --compare-to results/baseline

产物: evals/compression/results/<tag>/{manifest.json, summary.json, summary.md,
runs/*.json, manual_review_queue.json}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from evals.compression.driver import compress_fixture_messages, parse_fixture_messages
from evals.compression.fixture_loader import filter_fixtures, list_fixture_ids, load_fixture, load_probes
from evals.compression.grader import grade_single_probe
from evals.compression.policies import resolve_policy_options
from evals.compression.report import (
    UNCOMPACTED,
    build_summary,
    results_dir_for_tag,
    summarize_arm_runs,
    write_summary,
)
from evals.langfuse_env import eval_langfuse_run
from evals.manifest import (
    build_manifest,
    require_judge_separation,
    write_manifest,
    write_manual_review_queue,
)
from server.langfuse import eval_langfuse_observation

DEFAULT_ARMS = "uncompacted,current"


class _UsageTrackingLLM:
    """包一层 LLM 记录 usage_metadata（manifest 成本看板的数据源）。"""

    def __init__(self, inner):
        self.inner = inner
        self.input_tokens = 0
        self.output_tokens = 0

    def invoke(self, prompt):
        response = self.inner.invoke(prompt)
        usage = getattr(response, "usage_metadata", None)
        if isinstance(usage, dict):
            self.input_tokens += int(usage.get("input_tokens") or 0)
            self.output_tokens += int(usage.get("output_tokens") or 0)
        return response


def _resolve_tag(args: argparse.Namespace) -> str:
    return args.tag or os.environ.get("NOESIS_COMPRESSION_EVAL_TAG") or ""


def _resolve_runs(args: argparse.Namespace) -> int:
    if args.runs is not None:
        return max(1, int(args.runs))
    env_runs = os.environ.get("NOESIS_COMPRESSION_EVAL_RUNS")
    return max(1, int(env_runs)) if env_runs else 1


def _write_run_payload(tag: str, fixture_id: str, arm: str, run_index: int, payload: dict) -> None:
    out_dir = results_dir_for_tag(tag) / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{fixture_id}.{arm}.r{run_index}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_arm_once(
    *,
    fixture_id: str,
    arm: str,
    messages,
    probes: list[dict[str, Any]],
    fixture_options: dict[str, Any],
    eval_run_id: str,
    tag: str,
    answerer_llm,
    judge_llm,
) -> dict:
    session_id = f"eval-compression-{fixture_id}-{arm}-{eval_run_id}"
    with eval_langfuse_run(line="compression", tag=tag, session_id=session_id):
        with eval_langfuse_observation(
            name=f"compression/{fixture_id}/{arm}",
            input_data={"fixture_id": fixture_id, "arm": arm, "eval_run_id": eval_run_id},
        ):
            if arm == UNCOMPACTED:
                from evals.compression.driver import _approx_token_counter
                n_tokens = _approx_token_counter(messages)
                compression = {
                    "arm": UNCOMPACTED,
                    "compressed": False,
                    "pre_tokens": n_tokens,
                    "post_tokens": n_tokens,
                    "compression_ratio": 0.0,
                    "pre_message_count": len(messages),
                    "post_message_count": len(messages),
                    "summary_text": "",
                    "summary_marker_found": True,
                }
                context_messages = messages
                policy = None
            else:
                options = resolve_policy_options(fixture_options, arm)
                result = compress_fixture_messages(messages, compress_options=options)
                compression = {
                    key: result[key] for key in (
                        "compressed", "pre_tokens", "post_tokens", "compression_ratio",
                        "pre_message_count", "post_message_count", "summary_text",
                        "summary_marker_found",
                    )
                }
                compression["arm"] = arm
                context_messages = result["compressed_messages"]
                policy = arm

            probe_results = [
                grade_single_probe(
                    context_messages, probe,
                    answerer_llm=answerer_llm, judge_llm=judge_llm)
                for probe in probes
            ]
    return {
        "fixture_id": fixture_id,
        "arm": arm,
        "policy": policy,
        "eval_run_id": eval_run_id,
        "compression": compression,
        "probes": probe_results,
    }


def run_eval(args: argparse.Namespace) -> int:
    tag = _resolve_tag(args)
    if not tag:
        print("缺少 --tag 或 NOESIS_COMPRESSION_EVAL_TAG", file=sys.stderr)
        return 2
    require_judge_separation(args.model_id or "", args.judge_model_id)

    fixture_filter = args.fixture or os.environ.get("NOESIS_COMPRESSION_EVAL_FIXTURE")
    runs = _resolve_runs(args)
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]

    try:
        fixture_ids = filter_fixtures(list_fixture_ids(), fixture=fixture_filter)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    from noesis.llm import get_llm

    # 自定义模型：分别绑定后构造（端点随对象固定）；未提供 model-user 走内置目录
    if args.model_user:
        from evals.bootstrap import bind_user_model_sync
        judge_user = args.judge_model_user or args.model_user
        judge = _UsageTrackingLLM(get_llm(
            model_id=bind_user_model_sync(judge_user, args.judge_model_id)))
        # 被测绑定同时注入 summarization purpose：摘要引擎与作答同模型
        # （judge 分离只约束 judge ≠ 作答/摘要）
        answerer = _UsageTrackingLLM(get_llm(
            model_id=bind_user_model_sync(
                args.model_user, args.model_id, include_summarization=True)))
    else:
        answerer = _UsageTrackingLLM(get_llm(model_id=args.model_id or None))
        judge = _UsageTrackingLLM(get_llm(model_id=args.judge_model_id))

    eval_run_id = uuid.uuid4().hex[:16]
    all_arm_summaries = []
    review_records: list[dict[str, Any]] = []

    print(f"Compression eval tag={tag} fixtures={len(fixture_ids)} arms={arms} runs={runs}")
    for fixture_id in fixture_ids:
        fixture = load_fixture(fixture_id)
        try:
            probes_doc = load_probes(fixture_id)
        except FileNotFoundError:
            print(
                f"fixture {fixture_id} 缺 probe 题库：先运行 "
                f"`uv run python -m evals.compression.gen_probes --fixture {fixture_id}`",
                file=sys.stderr,
            )
            return 2
        messages = parse_fixture_messages(fixture["messages"])
        fixture_options = dict(fixture.get("compress_options") or {})

        for arm in arms:
            run_payloads = []
            for run_idx in range(runs):
                print(f"  {fixture_id} [{arm}] run {run_idx + 1}/{runs} ...", flush=True)
                payload = _run_arm_once(
                    fixture_id=fixture_id, arm=arm, messages=messages,
                    probes=probes_doc["probes"], fixture_options=fixture_options,
                    eval_run_id=eval_run_id, tag=tag,
                    answerer_llm=answerer, judge_llm=judge,
                )
                payload["run_index"] = run_idx
                run_payloads.append(payload)
                _write_run_payload(tag, fixture_id, arm, run_idx, payload)
                review_records.extend(
                    {"fixture_id": fixture_id, "arm": arm, "run_index": run_idx, **p}
                    for p in payload["probes"])

            summary = summarize_arm_runs(run_payloads)
            all_arm_summaries.append(summary)
            print(f"    recall%={summary.get('recall_pct')} "
                  f"retained={summary.get('retained_tokens')}")

    full_summary = build_summary(tag, all_arm_summaries, runs_per_arm=runs,
                                 compare_to=args.compare_to)
    json_path, md_path = write_summary(tag, full_summary)
    out_dir = results_dir_for_tag(tag)
    write_manifest(out_dir, build_manifest(
        eval_line="compression", tag=tag,
        subject_model=args.model_id or "(platform-default)",
        judge_model=args.judge_model_id,
        dataset={"fixtures": len(fixture_ids), "arms": arms, "runs_per_arm": runs},
        config={"arms": arms, "token_counter": "chars/4(content+tool_calls)",
                "judge_prompt_version": "recall-2-1-0+v5dims/v1"},
        usage={"input_tokens": answerer.input_tokens + judge.input_tokens,
               "output_tokens": answerer.output_tokens + judge.output_tokens},
    ))
    write_manual_review_queue(out_dir, review_records, seed=11)
    print(f"Results: {out_dir}")
    print(f"Summary: {json_path}")
    print(f"Report:  {md_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Noesis 消息压缩离线评测（多臂对照）")
    parser.add_argument("--tag", type=str, default=None, help="本次 run 标签（必填）")
    parser.add_argument("--fixture", type=str, default=None, help="仅跑指定 fixture id")
    parser.add_argument("--runs", type=int, default=None, help="同一 fixture×arm 重复次数（取中位数）")
    parser.add_argument("--arms", type=str, default=DEFAULT_ARMS,
                        help=f"逗号分隔评测臂：uncompacted + 策略名（默认 {DEFAULT_ARMS}）")
    parser.add_argument("--model-id", type=str, default=None, help="作答（continuation）模型")
    parser.add_argument("--judge-model-id", type=str, required=True,
                        help="判卷模型（须与作答模型不同）")
    parser.add_argument("--model-user", type=str, default="",
                        help="自定义模型归属用户（用户名或 id）；提供时经用户模型解析")
    parser.add_argument("--judge-model-user", type=str, default="",
                        help="judge 模型归属用户（缺省同 --model-user）")
    parser.add_argument("--compare-to", type=Path, default=None,
                        help="与历史 results/<tag> 目录对比")
    args = parser.parse_args(argv)
    return run_eval(args)


if __name__ == "__main__":
    raise SystemExit(main())
