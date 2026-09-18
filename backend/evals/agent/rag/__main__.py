"""CLI: Agent E2E 评测（跑被测链路 + 导出 ERB 官方判分格式）。

被测经 noesis CLI 子进程驱动（与 deepresearch/memory 线同构的 cli_driver
契约）：每题一个全新进程、走生产 headless 入口，会话/消息/token 落库到
`--eval-user` 账号（默认 test，前端可查）。本脚本只跑被测链路并自动导出
ERB 官方判分格式 `erb_answers.jsonl`（`question_id / answer / document_ids`，
检索文档名经 `evals/kb/erb_data/ingest_plan.json` 映射回官方 dsid），
不做任何自研判分——质量指标由 ERB 官方脚本产出。

用法（backend/ 下）:
    # ① 跑被测（本脚本）
    uv run python -m evals.agent.rag --model-id <m> --tag t1
    # 中断后续跑（同 tag 同数据集，已完成题自动跳过）：
    uv run python -m evals.agent.rag ... --tag t1 --resume
    # 只重跑 error 题：
    uv run python -m evals.agent.rag ... --tag t1 --resume --retry-failed

    # ② 官方判分（裁判模型自定，官方论文口径 GPT-5.4）：
    #    在 evals/agent/rag/erb_scorer/ 内执行（判分前官方先剥除引用标记）
    python -m src.scripts.answer_evaluation.metrics_based_eval \
        --answers-file <run 目录>/erb_answers.jsonl --parallelism 6

产物: evals/agent/rag/results/<tag>/{manifest.json, raw.jsonl, erb_answers.jsonl,
summary.json, summary.md}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.agent.rag.runner import run_agentic_rag_sample
from evals.agent.rag.to_erb import load_name_to_dsid, records_to_erb, write_erb_answers
from evals.manifest import (
    aggregate_usage,
    build_manifest,
    init_results_dir,
    write_manifest,
)

ROOT = Path(__file__).resolve().parent
RESULTS_ROOT = ROOT / "results"
DEFAULT_DATASET = ROOT / "fixtures" / "erb211.jsonl"
SAMPLE_SEED = 11


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


def load_raw_records(raw_path: Path) -> dict[str, dict[str, Any]]:
    """读 raw.jsonl，同一 sample_id 后写覆盖先写（断点续跑的增量日志语义）。"""
    records: dict[str, dict[str, Any]] = {}
    if not raw_path.is_file():
        return records
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            records[str(record.get("sample_id"))] = record
    return records


def append_raw_record(raw_path: Path, record: dict[str, Any]) -> None:
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with raw_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _is_error(record: dict[str, Any]) -> bool:
    return bool(record.get("error")) or not record.get("completed")


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(records)
    latencies = [r["latency_ms"] for r in records if r.get("latency_ms")]

    def rate(num: float, den: int) -> float:
        return round(num / den, 4) if den else 0.0

    return {
        "samples": n,
        "completed": sum(1 for r in records if r.get("completed")),
        "errors": sum(1 for r in records if _is_error(r)),
        "mean_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "total_input_tokens": sum(int(r.get("input_tokens") or 0) for r in records),
        "total_output_tokens": sum(int(r.get("output_tokens") or 0) for r in records),
    }


def _render_summary_md(summary: dict[str, Any], erb_path: Path) -> str:
    lines = [
        "# Agent E2E 评测 summary",
        "",
        f"- 样本 {summary['samples']}（完成 {summary['completed']} / error {summary['errors']}）",
        f"- 质量指标由 ERB 官方脚本判分产出，本文件只汇总运行健康度",
        "",
        "| 运行指标 | 值 |",
        "|---|---:|",
        f"| 平均延迟 | {summary['mean_latency_ms']} ms |",
        f"| 总 input tokens | {summary['total_input_tokens']} |",
        f"| 总 output tokens | {summary['total_output_tokens']} |",
        "",
        f"官方判分输入: `{erb_path}`",
    ]
    return "\n".join(lines) + "\n"


async def _run(args: argparse.Namespace) -> int:
    dataset_path = Path(args.dataset)
    if not dataset_path.is_absolute():
        dataset_path = ROOT / dataset_path
    rows = load_dataset(dataset_path)
    if args.sample:
        rng = random.Random(SAMPLE_SEED)
        rows = rng.sample(rows, min(args.sample, len(rows)))

    out_dir = (Path(args.output) if args.output else init_results_dir(RESULTS_ROOT, args.tag,
                                 allow_resume=args.resume or args.retry_failed))
    raw_path = out_dir / "raw.jsonl"
    done = load_raw_records(raw_path) if args.resume else {}

    # 待跑样本：resume 跳过已完成；--retry-failed 只重跑 error 题
    if args.retry_failed:
        todo = [r for r in rows if str(r["id"]) not in done or _is_error(done[str(r["id"])])]
    else:
        todo = [r for r in rows if str(r["id"]) not in done]
    print(f"dataset={dataset_path.name} samples={len(rows)} "
          f"done={len(done) if args.resume else 0} todo={len(todo)} → {out_dir}")

    from evals.langfuse_env import eval_langfuse_run

    subject_model_id = args.model_id or None
    total = len(todo)
    done_before = len(done) if args.resume else 0
    t0 = time.perf_counter()
    for i, sample in enumerate(todo, 1):
        sample_id = str(sample["id"])
        print(f"--- {sample_id}", flush=True)
        if args.model_user:
            subject_model_id = await bind_user_model(
                args.model_user, args.model_id, include_summarization=True)
        with eval_langfuse_run(line="agent", tag=args.tag,
                               session_id=f"agentic-rag-{sample_id}"):
            result = await run_agentic_rag_sample(
                sample,
                time_budget_seconds=args.time_budget,
                model_id=subject_model_id,
                eval_user=args.eval_user,
            )
        record: dict[str, Any] = {
            "sample_id": sample_id,
            "question_id": sample_id,
            "query": sample["query"],
            "session_id": result.get("session_id"),
            "completed": result.get("completed"),
            "error": result.get("error"),
            "final_text": result.get("final_text") or "",
            "tool_stats": result.get("tool_stats") or {},
            "tool_outputs": result.get("tool_outputs") or [],
            "kb_refs": result.get("kb_refs") or [],
            "input_tokens": result.get("input_tokens") or 0,
            "output_tokens": result.get("output_tokens") or 0,
            "latency_ms": result.get("latency_ms") or 0,
        }
        append_raw_record(raw_path, record)
        progress = done_before + i
        elapsed = time.perf_counter() - t0
        eta_min = elapsed / progress * (total - progress) / 60 if progress else 0
        print(f"    [{progress}/{total}] completed={record['completed']} "
              f"（累计 {elapsed/60:.1f} 分钟，预计还需 {eta_min:.0f} 分钟）", flush=True)

    from evals.agent.rag.runner import _close_http
    await _close_http()

    # 汇总（last-record-wins）+ 导出 ERB 官方判分格式
    all_records = list(load_raw_records(raw_path).values())
    summary = _summarize(all_records)
    erb_path = out_dir / "erb_answers.jsonl"
    erb_records, unmapped = records_to_erb(all_records, load_name_to_dsid())
    write_erb_answers(erb_records, unmapped, erb_path)

    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "summary.md").write_text(
        _render_summary_md(summary, erb_path), encoding="utf-8")
    write_manifest(out_dir, build_manifest(
        eval_line="agent-rag", tag=args.tag,
        subject_model=subject_model_id or args.model_id,
        dataset={"path": str(dataset_path), "count": len(rows), "sample_seed": SAMPLE_SEED},
        config={"time_budget_s": args.time_budget,
                "erb_answers": str(erb_path),
                "retry_failed": bool(args.retry_failed)},
        usage=aggregate_usage(all_records),
    ))
    print(json.dumps({**summary, "erb_answers": str(erb_path)}, ensure_ascii=False, indent=2))
    return 0 if all(r.get("completed") for r in all_records) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent E2E 评测（被测链路 + ERB 导出）")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--model-id", required=True, help="被测模型（如 glm-5.3-flash）")
    parser.add_argument("--model-user", default="",
                        help="自定义模型归属用户（用户名或 id）；提供时经用户模型解析，未命中即报错")
    parser.add_argument("--eval-user", default="test",
                        help="评测会话归属账号（用户名，须为真实账号；默认 test）")
    parser.add_argument("--tag", default=f"run-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}")
    parser.add_argument("--output", default="", help="产物目录覆盖（默认 results/<tag>/）")
    parser.add_argument("--sample", type=int, default=0, help="抽样题数（种子固定）")
    parser.add_argument("--time-budget", type=int, default=180)
    parser.add_argument("--resume", action="store_true", help="续跑：跳过已完成题")
    parser.add_argument("--retry-failed", action="store_true", help="只重跑 error 题")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
