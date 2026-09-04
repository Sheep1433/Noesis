"""CLI: Agent E2E 评测（判卷 + 引用溯源 + 失败归因）。

用法（backend/ 下）:
    uv run python -m evals.agent.rag --sample 10 --model-id <m> --judge-model-id <j> --tag t1
    # 中断后续跑（同 tag 同数据集，已完成题自动跳过）：
    uv run python -m evals.agent.rag ... --tag t1 --resume
    # 只重跑 error 题：
    uv run python -m evals.agent.rag ... --tag t1 --resume --retry-failed

产物: evals/agent/rag/results/<tag>/{manifest.json, raw.jsonl, summary.json, summary.md,
attribution.md, manual_review_queue.json}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.agent.rag.judge import JUDGE_PROMPT_VERSION, VERDICT_SCORES, judge_answer
from evals.agent.rag.runner import run_agentic_rag_sample
from evals.agent.citation import citation_metrics, judge_fact_grounding
from evals.bootstrap import agentic_rag_runtime, bind_user_model
from evals.manifest import (
    aggregate_usage,
    build_manifest,
    init_results_dir,
    require_judge_separation,
    write_manifest,
    write_manual_review_queue,
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


async def _retrieval_hits_for(
    records: list[dict[str, Any]], kb_results: Path | None
) -> dict[str, bool]:
    """归因用的检索命中：优先 kb 评测 raw（同题 gt_rank），缺则现场补一次检索。"""
    by_question: dict[str, bool] = {}
    if kb_results and kb_results.is_file():
        data = json.loads(kb_results.read_text(encoding="utf-8"))
        rank_by_qid = {p["question_id"]: p.get("gt_rank") for p in data.get("positives") or []}
        by_question = {
            r["question_id"]: rank_by_qid.get(r["question_id"]) is not None
            for r in records
            if r.get("question_id") in rank_by_qid
        }
    missing = [r for r in records if r["question_id"] not in by_question]
    if missing:
        from evals.kb.erb import load_data
        from evals.kb.metrics import gt_rank
        from noesis.knowledge.retrieval.service import KbRetrievalService

        _, name_to_dsid = load_data()
        for r in missing:
            res = KbRetrievalService.search(
                collection_name="erb-eval", query=r["query"],
                score_threshold=0.0, final_top_k=10)
            rows = [{"file": h.file_name, "score": h.rerank_score or h.score} for h in res.hits]
            rank = gt_rank(rows, name_to_dsid, r.get("expected_doc_ids") or [])
            by_question[r["question_id"]] = rank is not None
    return by_question


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(records)
    judged = [r for r in records if (r.get("judge") or {}).get("verdict") in VERDICT_SCORES]
    invalid = [r for r in records if (r.get("judge") or {}).get("verdict") == "invalid"]
    citation_rows = [r for r in records if r.get("citation")]
    grounded = [r["fact_grounding"]["grounding_rate"] for r in records
                if r.get("fact_grounding", {}).get("grounding_rate") is not None]
    acc = [r["citation"]["citation_accuracy"] for r in citation_rows
           if r["citation"].get("citation_accuracy") is not None]

    def rate(num: float, den: int) -> float:
        return round(num / den, 4) if den else 0.0

    return {
        "samples": n,
        "completed": sum(1 for r in records if r.get("completed")),
        "errors": sum(1 for r in records if _is_error(r)),
        "kb_tool_call_rate": rate(sum(1 for r in records if r.get("kb_tool_called")), n),
        "mean_source_recall": rate(
            sum(float((r.get("source_score") or {}).get("source_recall") or 0) for r in records), n),
        "task_success_rate_full": rate(
            sum(1 for r in judged if r["judge"]["verdict"] == "accepted"), len(judged)),
        "task_success_rate_half": rate(
            sum(VERDICT_SCORES[r["judge"]["verdict"]] for r in judged), len(judged)),
        "judged": len(judged),
        "judge_invalid": len(invalid),
        "citation_format_compliant_rate": rate(
            sum(1 for r in citation_rows if r["citation"].get("format_compliant")),
            len(citation_rows)),
        "mean_citation_accuracy": round(sum(acc) / len(acc), 4) if acc else None,
        "mean_fact_grounding_rate": round(sum(grounded) / len(grounded), 4) if grounded else None,
        "judge_prompt_version": JUDGE_PROMPT_VERSION,
    }


def _render_summary_md(summary: dict[str, Any]) -> str:
    lines = [
        "# Agent E2E 评测 summary",
        "",
        f"- 样本 {summary['samples']}（完成 {summary['completed']} / error {summary['errors']}）",
        "",
        "| 指标 | 值 |",
        "|---|---:|",
        f"| 任务成功率（仅采纳） | {summary['task_success_rate_full']:.1%} |",
        f"| 任务成功率（部分采纳折半） | {summary['task_success_rate_half']:.1%} |",
        f"| KB 工具调用率 | {summary['kb_tool_call_rate']:.1%} |",
        f"| 期望来源 recall（均值） | {summary['mean_source_recall']:.1%} |",
        f"| 引用格式遵循率 | {summary['citation_format_compliant_rate']:.1%} |",
        f"| 引用正确率（均值） | {summary['mean_citation_accuracy']}",
        f"| 事实可溯源率（均值） | {summary['mean_fact_grounding_rate']}",
        f"| judge 解析失败 | {summary['judge_invalid']}/{summary['judged'] + summary['judge_invalid']} |",
    ]
    return "\n".join(lines) + "\n"


async def _run(args: argparse.Namespace) -> int:
    require_judge_separation(args.model_id or "", args.judge_model_id)
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
    from noesis.llm import get_llm

    # judge 对象在 judge 模型绑定期间构造一次（端点随对象固定，后续重绑不影响）
    judge_user = args.judge_model_user or args.model_user
    if judge_user:
        judge_snapshot_id = await bind_user_model(judge_user, args.judge_model_id)
    else:
        judge_snapshot_id = args.judge_model_id
    judge_llm = get_llm(model_id=judge_snapshot_id)

    subject_model_id = args.model_id or None
    async with agentic_rag_runtime():
        for sample in todo:
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
                )
            record: dict[str, Any] = {
                "sample_id": sample_id,
                "question_id": sample_id,
                "query": sample["query"],
                "expected_doc_ids": sample.get("expected_doc_ids") or [],
                "expected_sources": sample.get("expected_sources") or [],
                "gold_answer": sample.get("gold_answer") or "",
                "answer_facts": sample.get("answer_facts") or [],
                "completed": result.get("completed"),
                "error": result.get("error"),
                "final_text": result.get("final_text") or "",
                "tool_stats": result.get("tool_stats") or {},
                "tool_outputs": result.get("tool_outputs") or [],
                "kb_tool_called": result.get("kb_tool_called"),
                "source_score": result.get("source_score"),
                "input_tokens": result.get("input_tokens") or 0,
                "output_tokens": result.get("output_tokens") or 0,
                "latency_ms": result.get("latency_ms") or 0,
            }
            if record["completed"] and record["gold_answer"]:
                record["judge"] = judge_answer(
                    question=record["query"],
                    gold_answer=record["gold_answer"],
                    answer=record["final_text"],
                    llm=judge_llm,
                )
            record["citation"] = citation_metrics(
                record["final_text"],
                expected_doc_files=sample.get("expected_sources") or [],
            )
            if record["completed"] and record["answer_facts"] and record["citation"]["cited_kb_files"]:
                record["fact_grounding"] = judge_fact_grounding(
                    answer_facts=record["answer_facts"],
                    tool_outputs=record["tool_outputs"],
                    cited_files=record["citation"]["cited_kb_files"],
                    llm=judge_llm,
                )
            append_raw_record(raw_path, record)
            verdict = (record.get("judge") or {}).get("verdict", "-")
            print(f"    completed={record['completed']} judge={verdict} "
                  f"citation_format={record['citation']['format_compliant']}", flush=True)

        # 汇总（last-record-wins）与归因
        all_records = list(load_raw_records(raw_path).values())
        summary = _summarize(all_records)
        rejected = [r for r in all_records if (r.get("judge") or {}).get("verdict") == "rejected"]
        if rejected:
            from evals.agent.rag.attribution import (
                attribute_failures,
                render_attribution_md,
            )
            hits = await _retrieval_hits_for(rejected, args.kb_results)
            for r in rejected:
                r["retrieval_hit"] = hits.get(r["question_id"])
            attribution = attribute_failures(rejected)
            (out_dir / "attribution.md").write_text(
                render_attribution_md(attribution), encoding="utf-8")
            summary["attribution_counts"] = attribution["counts"]
        else:
            attribution = None

    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "summary.md").write_text(_render_summary_md(summary), encoding="utf-8")
    write_manifest(out_dir, build_manifest(
        eval_line="agent-rag", tag=args.tag,
        subject_model=subject_model_id or args.model_id, judge_model=args.judge_model_id,
        dataset={"path": str(dataset_path), "count": len(rows), "sample_seed": SAMPLE_SEED},
        config={"time_budget_s": args.time_budget, "judge_prompt_version": JUDGE_PROMPT_VERSION,
                "kb_results": str(args.kb_results) if args.kb_results else None,
                "retry_failed": bool(args.retry_failed)},
        usage=aggregate_usage(all_records),
    ))
    write_manual_review_queue(out_dir, all_records, seed=SAMPLE_SEED)
    print(json.dumps({**summary, "output": str(out_dir)}, ensure_ascii=False, indent=2))
    return 0 if all(r.get("completed") for r in all_records) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Noesis Agent E2E 评测（判卷/引用/归因）")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--model-id", required=True, help="被测模型（如 glm-5.3-flash）")
    parser.add_argument("--judge-model-id", required=True, help="judge 模型（须与被测不同）")
    parser.add_argument("--model-user", default="",
                        help="自定义模型归属用户（用户名或 id）；提供时经用户模型解析，未命中即报错")
    parser.add_argument("--judge-model-user", default="",
                        help="judge 模型归属用户（缺省同 --model-user）")
    parser.add_argument("--tag", default=f"run-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}")
    parser.add_argument("--output", default="", help="产物目录覆盖（默认 results/<tag>/）")
    parser.add_argument("--sample", type=int, default=0, help="抽样题数（种子固定）")
    parser.add_argument("--time-budget", type=int, default=180)
    parser.add_argument("--resume", action="store_true", help="续跑：跳过已完成题")
    parser.add_argument("--retry-failed", action="store_true", help="只重跑 error 题")
    parser.add_argument("--kb-results", type=Path, default=None,
                        help="kb 评测 raw.json 路径（归因用检索命中；缺则现场补检索）")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
