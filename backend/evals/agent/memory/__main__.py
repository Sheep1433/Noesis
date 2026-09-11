"""CLI: 记忆召回行为评测（LongMemEval 三层指标 + 自建负例 / 冒烟模式）。

用法（backend/ 下）:
    # LongMemEval S 档抽样 30 题（正例三层 + 每 5 题一条配对负例）
    uv run python -m evals.agent.memory \
        --model-id <m> --judge-model-id <j> --tag t1 [--sample 30]
    # 续跑 / 只重跑 error 题
    uv run python -m evals.agent.memory ... --tag t1 --resume [--retry-failed]
    # 旧四场景冒烟（不依赖 LongMemEval 数据）
    uv run python -m evals.agent.memory --mode smoke --model-id <m> --judge-model-id <j>

产物: evals/agent/memory/results/<tag>/{manifest.json, raw.jsonl, summary.json, summary.md,
manual_review_queue.json}
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.agent.memory.fixtures import (
    EVAL_USER_ID,
    NEGATIVE_QUERIES,
    NEGATIVE_SCENARIOS,
    RECALL_SCENARIOS,
)
from evals.agent.memory.metrics import summarize_memory_eval
from evals.agent.memory.runner import (
    run_longmemeval_positive,
    run_memory_recall_sample,
    run_negative_sample,
    seed_eval_memory,
)
from evals.agent.rag.judge import judge_answer
from evals.agent.rag.__main__ import (
    _is_error,
    append_raw_record,
    load_raw_records,
)
from evals.bootstrap import bind_user_model_sync
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


def _render_summary_md(summary: dict[str, Any]) -> str:
    judged_display = f"{summary['answer_accepted_rate']:.1%}" if summary["judged"] else "-"
    half_display = f"{summary['answer_score_rate_half']:.1%}" if summary["judged"] else "-"
    breakdown = summary.get("error_breakdown") or {}
    error_note = (
        "：".join(f"{k} {v}" for k, v in breakdown.items())
        if breakdown else "无")
    invalid_note = f"，judge 解析失败 {summary['judge_invalid']}" if summary["judge_invalid"] else ""
    lines = [
        "# 记忆召回评测 summary",
        "",
        f"- 样本 {summary['samples']}（正例 {summary['positives']} / 负例 {summary['negatives']}"
        f" / error {summary['errors']}，其中 {error_note}{invalid_note}）",
        "",
        "| 指标 | 值 |",
        "|---|---:|",
        f"| 答案正确率（judge 采纳） | {judged_display} |",
        f"| 答案得分率（部分采纳折半） | {half_display} |",
        f"| 条目级 recall@k（均值） | {summary['mean_recall@k']} |",
        f"| 条目级 precision@k（均值） | {summary['mean_precision@k']} |",
        f"| 行为级召回率（主动调用 search_memory） | {summary['behavior_recall_rate']:.1%} |",
        f"| 负例误召回率 | {summary['negative_false_recall_rate']:.1%} |",
    ]
    if summary.get("by_question_type"):
        lines.extend(["", "## 分题型", "", "| 题型 | 采纳/题数 |", "|---|---|"])
        for qtype, row in sorted(summary["by_question_type"].items()):
            lines.append(f"| {qtype} | {row['accepted']}/{row['n']} |")
    return "\n".join(lines) + "\n"


def _run(args: argparse.Namespace) -> int:
    require_judge_separation(args.model_id or "", args.judge_model_id)
    out_dir = init_results_dir(RESULTS_ROOT, args.tag,
                                 allow_resume=args.resume or args.retry_failed)
    raw_path = out_dir / "raw.jsonl"
    done = load_raw_records(raw_path) if args.resume else {}

    from evals.langfuse_env import eval_langfuse_run
    from noesis.llm import get_llm

    # judge 在宿主机侧运行（单次 LLM 调用，不进 CLI 子进程），沿用用户模型
    # 绑定；被测模型走 CLI 子进程的 env 直连（evals/.env 的 NOESIS_*），
    # --model-id 即端点真实模型名
    judge_user = args.judge_model_user or args.model_user
    if judge_user:
        judge_snapshot_id = bind_user_model_sync(judge_user, args.judge_model_id)
    else:
        judge_snapshot_id = args.judge_model_id
    judge_llm = get_llm(model_id=judge_snapshot_id)
    subject_model = args.model_id
    todo_meta: list[dict[str, Any]] = []  # {kind: positive|negative, payload}

    if args.mode == "smoke":
        seed_eval_memory(args.user_id)
        for scenario in RECALL_SCENARIOS:
            todo_meta.append({"kind": "positive", "payload": scenario})
        for scenario in NEGATIVE_SCENARIOS:
            todo_meta.append({
                "kind": "negative",
                "payload": {**scenario, "user_id": args.user_id},
            })
    else:
        from evals.agent.memory.longmemeval import eval_user_id, load_questions

        questions = load_questions(sample=args.sample or 30)
        for i, question in enumerate(questions):
            todo_meta.append({"kind": "positive", "payload": question})
            # 配对负例：绑定到同题隔离用户（构造时确定，resume 跳过正例后
            # 负例仍能定位到自己的 haystack，不依赖本进程执行顺序）
            if args.negative_every and (i + 1) % args.negative_every == 0:
                todo_meta.append({
                    "kind": "negative",
                    "payload": {
                        "id": f"neg-{question['question_id']}",
                        "user_id": eval_user_id(question["question_id"]),
                        "query": NEGATIVE_QUERIES[(i // args.negative_every) % len(NEGATIVE_QUERIES)],
                    },
                })

    # resume：跳过已完成；retry-failed 只重跑 error
    if args.retry_failed:
        todo_meta = [t for t in todo_meta
                     if str(t["payload"].get("id") or t["payload"].get("question_id")) not in done
                     or _is_error(done[str(t["payload"].get("id") or t["payload"].get("question_id"))])]
    else:
        todo_meta = [t for t in todo_meta
                     if str(t["payload"].get("id") or t["payload"].get("question_id")) not in done]

    print(f"mode={args.mode} tag={args.tag} todo={len(todo_meta)} → {out_dir}")

    async def _run_samples() -> None:
        for item in todo_meta:
            payload = item["payload"]
            with eval_langfuse_run(line="agent", tag=args.tag,
                                   session_id=f"memory-eval-{args.tag}"):
                if item["kind"] == "positive":
                    if args.mode == "smoke":
                        record = await run_memory_recall_sample(
                            payload, user_id=payload.get("user_id") or args.user_id,
                            time_budget_seconds=args.time_budget,
                            model_id=subject_model or None)
                        record["question_type"] = "smoke"
                    else:
                        record = await run_longmemeval_positive(
                            payload, model_id=subject_model or None,
                            time_budget_seconds=args.time_budget)
                    # 层 1：judge 判卷（gold answer）——超时题只要有 final_text
                    # 也判卷，预算问题不应白白丢掉已产出的作答
                    if record.get("final_text") and record.get("answer"):
                        record["judge"] = judge_answer(
                            question=record.get("question") or record.get("query"),
                            gold_answer=record["answer"],
                            answer=record["final_text"],
                            llm=judge_llm,
                        )
                else:
                    record = await run_negative_sample(
                        user_id=payload["user_id"], query=payload["query"],
                        time_budget_seconds=args.time_budget,
                        model_id=subject_model or None,
                        forbidden_snippets=payload.get("forbidden_snippets"),
                        sample_id=str(payload.get("id") or ""))
            append_raw_record(raw_path, record)
            verdict = (record.get("judge") or {}).get("verdict", "-")
            print(f"  {record['sample_id']} kind={'neg' if record['negative'] else 'pos'} "
                  f"completed={record['completed']} judge={verdict} "
                  f"recall@k={(record.get('retrieval') or {}).get('recall@k')}", flush=True)

    # 每样本一个 CLI 子进程，无跨样本共享状态；judge 调用是同步 LLM 请求，
    # 阻塞事件循环无碍（循环内无并发任务）
    asyncio.run(_run_samples())

    all_records = list(load_raw_records(raw_path).values())
    summary = summarize_memory_eval(all_records)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "summary.md").write_text(_render_summary_md(summary), encoding="utf-8")
    write_manifest(out_dir, build_manifest(
        eval_line="agent-memory", tag=args.tag,
        subject_model=args.model_id, judge_model=args.judge_model_id,
        dataset={"mode": args.mode, "count": len(all_records),
                 "negative_every": args.negative_every},
        config={"time_budget_s": args.time_budget, "sample": args.sample,
                "user_id": args.user_id if args.mode == "smoke" else None,
                "model_user": args.model_user or None,
                "judge_model_user": args.judge_model_user or args.model_user or None},
        usage=aggregate_usage(all_records),
    ))
    positives = [r for r in all_records if not r.get("negative")]
    write_manual_review_queue(out_dir, positives, seed=11)
    print(json.dumps({**summary, "output": str(out_dir)}, ensure_ascii=False, indent=2))
    ok = all(r.get("completed") for r in all_records) and (
        not any(r.get("violation") for r in all_records if r.get("negative")))
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Noesis 记忆召回行为评测（三层指标）")
    parser.add_argument("--mode", choices=["longmemeval", "smoke"], default="longmemeval")
    parser.add_argument("--model-id", required=True, help="被测模型")
    parser.add_argument("--judge-model-id", required=True, help="judge 模型（须与被测不同）")
    parser.add_argument("--tag", default=f"run-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}")
    parser.add_argument("--sample", type=int, default=30, help="LongMemEval 抽样题数")
    parser.add_argument("--negative-every", type=int, default=5,
                        help="每 N 个正例跑一条配对负例（0 关闭）")
    parser.add_argument("--time-budget", type=int, default=600,
                        help="单题时间预算（秒）；LongMemEval haystack 大，实测 240s 大概率超时")
    parser.add_argument("--user-id", default=EVAL_USER_ID, help="smoke 模式评测用户")
    parser.add_argument("--model-user", default="",
                        help="自定义模型归属用户（用户名或 id）；提供时经用户模型解析，未命中即报错")
    parser.add_argument("--judge-model-user", default="",
                        help="judge 模型归属用户（缺省同 --model-user）")
    parser.add_argument("--resume", action="store_true", help="续跑：跳过已完成题")
    parser.add_argument("--retry-failed", action="store_true", help="只重跑 error 题")
    args = parser.parse_args()
    raise SystemExit(_run(args))


if __name__ == "__main__":
    main()
