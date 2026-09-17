"""DeepResearch Bench（中文 5 题固定子集）：SuperAgent 生成调研报告。

用法（backend/ 下）:
    NOESIS_WEB_PROXY=http://127.0.0.1:7897 \
    uv run python -m evals.agent.deepresearch --tag smoke-1p --limit 1

- 题库：fixtures/tasks-5.json（HuggingFace muset-ai/DeepResearch-Bench-Dataset
  的确定性子集，Apache-2.0）
- 产物：results/<tag>/{articles.jsonl, summary.json}；articles.jsonl 为
  {id, prompt, article} 结构，可直接喂官方 RACE/FACT 判分脚本
- 判分（RACE 报告质量 / FACT 引用可信度）暂未接入，先产出报告原文
- 被测对象经 noesis CLI 子进程驱动（每题一个进程，沙箱由配置层按评测进程
  强制 local_shell，不产生 runner 沙箱容器）；CLI 走生产 headless 入口：消息/run/token 落
  DB（--eval-user 账号下），被测模型经 --model-id 指定（须为内置目录 id
  或该账号的自定义模型复合 id，如 huoshan/glm-5.3-flash）
- 环境要求：--eval-user 须为真实账号（默认 test）——子 Agent 会话血缘按
  user_id 写库，假用户名会导致派发失败主 Agent 单干；NOESIS_WEB_PROXY
  为 web_fetch 的代理回退（直连失败时自动走代理重试）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from evals.agent.cli_driver import run_cli_agent
from evals.langfuse_env import eval_langfuse_run

ROOT = Path(__file__).resolve().parent
RESULTS_ROOT = ROOT / "results"
DEFAULT_TASKS = ROOT / "fixtures" / "tasks-5.json"

# 评测模式说明：单回合运行没有生产环境的后台任务通知回合，
# 不附加则 Agent 可能委派后台子 Agent 后直接结束回合，交付物变成
# 「已转入后台」的状态说明。附加在题目后发给 Agent；articles.jsonl
# 记录的 prompt 保持官方原文。
EVAL_MODE_SUFFIX = (
    "\n\n（评测模式说明：本次运行为单回合评测，不存在后台任务完成后的通知回合。"
    "如需子 Agent，一律 run_in_background=false 前台等待其完成后继续；"
    "不要把任务留在后台轮询。\n"
    "交付契约——最终报告必须以以下两种形态之一交付，缺一即视为未完成：\n"
    "1. 主形态：报告全文写在你的最后一条消息里（判分只读最后一条消息，"
    "过程中的计划/叙述不会被采集）；\n"
    "2. 备选形态：报告全文写入 /workspace/final-report.md，"
    "并在最后一条消息中注明「报告已写入该文件」。\n"
    "不要把「已转入后台/稍后交付」作为最终输出。）"
)


def load_tasks(path: Path, limit: int | None = None) -> list[dict]:
    tasks = json.loads(Path(path).read_text(encoding="utf-8"))
    if not tasks:
        raise ValueError(f"题库为空: {path}")
    return tasks[:limit] if limit else tasks


def recollect_from_raw(args, out: Path, articles_path: Path) -> int:
    """从 raw/ 原始流重建 articles.jsonl：driver 层采集故障的零成本恢复。

    底账逐行含 ``__tw_result__``（CLI 组装的 DB 权威终值），直接取用。
    """
    from evals.agent.cli_driver import _HARVEST_MIN_CHARS, _REPORT_FILENAME
    from noesis.config.user_data_paths import get_workspace_dir

    raw_dir = out / "raw"
    if not raw_dir.is_dir():
        print(f"无原始流可重收集: {raw_dir}", file=sys.stderr)
        return 2
    tasks = {t["id"]: t for t in load_tasks(args.tasks)}
    records = []
    for raw_file in sorted(raw_dir.glob("*.jsonl")):
        try:
            task_id = int(raw_file.stem)
        except ValueError:
            continue
        task = tasks.get(task_id)
        if task is None:
            continue
        db_result = None
        sid = f"unknown-{task_id}"
        uid = ""
        for line in raw_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue  # SSE 帧（event:/data:）与 [DONE] 非 JSON 行
            # 会话/用户标识在 __tw_init__ 行，终值在 __tw_result__ 行
            if obj.get("type") == "__tw_init__":
                sid = str(obj.get("session_id") or sid)
                uid = str(obj.get("user_id") or "")
            elif obj.get("type") == "__tw_result__":
                db_result = obj
        record = dict(db_result or {})
        record.setdefault("final_text", "")
        record.setdefault("error", None)
        if not uid:
            uid = eval_user_id_of(args)
        if not record.get("error") and len(str(record.get("final_text") or "")) < _HARVEST_MIN_CHARS:
            report = Path(get_workspace_dir(uid, sid)) / _REPORT_FILENAME
            if report.is_file() and report.stat().st_size > _HARVEST_MIN_CHARS:
                try:
                    record["final_text"] = report.read_text(encoding="utf-8")
                    record["article_source"] = f"workspace_file:{_REPORT_FILENAME}"
                except OSError:
                    pass
        records.append({
            "id": task_id,
            "topic": task["topic"],
            "language": task["language"],
            "prompt": task["prompt"],
            "article": record.get("final_text") or "",
            "session_id": sid,
            "elapsed_seconds": 0,
            "error": record.get("error"),
        })
    with articles_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"重收集完成：{len(records)} 题 -> {articles_path}")
    return 0


def eval_user_id_of(args) -> str:
    from evals.bootstrap import resolve_user_uuid_sync

    return resolve_user_uuid_sync(args.eval_user)


def score_existing(args, out: Path, articles_path: Path) -> int:
    """RACE 判分：对已有 articles.jsonl 打分（不跑题）。"""
    from evals.agent.deepresearch import race

    if not args.judge_model_id:
        print("判分需要 --judge-model-id", file=sys.stderr)
        return 2
    if not articles_path.is_file():
        print(f"无作答产物可判分: {articles_path}", file=sys.stderr)
        return 2
    refs, crits = race.load_race_data()

    from langchain_core.messages import HumanMessage
    from noesis.llm import get_llm

    judge_model = args.judge_model_id
    if args.judge_model_user:
        from evals.bootstrap import bind_user_model_sync

        judge_model = bind_user_model_sync(args.judge_model_user, args.judge_model_id)
    llm = get_llm(model_id=judge_model)

    records = [json.loads(line) for line in articles_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    scores_path = out / "scores.jsonl"
    scored: list[dict] = []
    for i, rec in enumerate(records, 1):
        sid = rec.get("id")
        ref, crit = refs.get(sid), crits.get(sid)
        if rec.get("error") or not rec.get("article"):
            print(f"[{i}/{len(records)}] id={sid} 跳过（无报告）")
            continue
        if not ref or not crit:
            print(f"[{i}/{len(records)}] id={sid} 跳过（缺 RACE 数据）")
            continue
        print(f"[{i}/{len(records)}] id={sid} 判分中 ...", flush=True)
        prompt = race.build_race_prompt(
            rec["prompt"], rec["article"], ref["article"], crit)
        result = None
        for _attempt in range(3):
            raw = str(llm.invoke([HumanMessage(content=prompt)]).content or "")
            try:
                judge_output = race.parse_judge_output(raw)
            except (ValueError, json.JSONDecodeError):
                continue
            result = race.race_record(
                task_prompt=rec["prompt"], article_ours=rec["article"],
                reference=ref, criteria=crit, judge_output=judge_output)
            break
        if result is None:
            result = {"id": sid, "overall_score": None,
                      "error": "judge output unparseable after retries"}
        result = {"id": sid, **result}
        scored.append(result)
        with scores_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
        print(f"    overall={result.get('overall_score')}")

    valid = [s for s in scored if s.get("overall_score") is not None]
    summary_extra = {
        "race_judge_model": args.judge_model_id,
        "race_scored": len(valid),
        "race_parse_failures": len(scored) - len(valid),
        "race_overall_mean_pct": round(
            sum(s["overall_score"] for s in valid) / len(valid) * 100, 2
        ) if valid else None,
        "race_dims_mean_pct": {
            d: round(sum(s.get(d, 0) for s in valid) / len(valid) * 100, 2)
            for d in race.DIMS
        } if valid else {},
    }
    summary_path = out / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    summary.update(summary_extra)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"RACE: {summary_extra['race_scored']} 题有效，"
          f"overall 均值 {summary_extra['race_overall_mean_pct']}%（50=与专家参考持平）")
    print(f"Scores: {scores_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="DeepResearch Bench（SuperAgent 调研报告）")
    p.add_argument("--tag", required=True)
    p.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    p.add_argument("--limit", type=int, default=None, help="只跑前 N 题（冒烟/成本试跑）")
    p.add_argument("--time-budget", type=int, default=2700,
                   help="单题时间预算（秒）；子 Agent 深度调研 + 收结果，"
                        "冒烟实测 1200s 不够")
    p.add_argument("--model-id", default=None,
                   help="被测模型（内置目录 id 或评测账号的自定义模型复合 id，"
                        "如 huoshan/glm-5.3-flash；缺省走会话/用户偏好/平台默认）")
    p.add_argument("--eval-user", default="test",
                   help="评测数据归属账号（用户名或 UUID）；须为真实账号——"
                        "子 Agent 会话血缘按 user_id 写库，假用户名会导致派发失败")
    p.add_argument("--recollect", action="store_true",
                   help="重收集模式：不跑题，从 results/<tag>/raw/ 的原始流"
                        "重建 articles.jsonl（采集逻辑升级/结果误删后零成本恢复）")
    p.add_argument("--score", action="store_true",
                   help="判分模式：不跑题，对 results/<tag>/articles.jsonl "
                        "按 RACE 官方口径（相对分，50=与专家参考持平）打分")
    p.add_argument("--judge-model-id", default=None, help="RACE 判分模型")
    p.add_argument("--judge-model-user", default=None, help="判分模型归属用户")
    args = p.parse_args(argv)

    from evals.bootstrap import resolve_user_uuid_sync

    eval_user_id = resolve_user_uuid_sync(args.eval_user)

    # 子 Agent 前台等待窗口经 env 注入 CLI 子进程（SUBAGENT_*_SECONDS）：
    # 生产默认 600s（10 分钟）超时即自动转后台，评测单回合没有通知回合，主 Agent 只能
    # 轮询收结果——每次轮询都是全量上下文的 LLM 调用，冒烟实测 1200s
    # 预算被轮询耗尽且未交卷。前台等待发生在工具调用内部，不烧 LLM 轮次。
    # task_timeout 同步放宽：子 Agent 硬超时的 CancelledError 会穿透
    # 前台等待盾牌炸掉主运行（冒烟实测），深度调研子 Agent 需要更长窗口。
    subagent_env = {
        "SUBAGENT_TASK_TIMEOUT_SECONDS": "1800",
        "SUBAGENT_FOREGROUND_MAX_WAIT_SECONDS": "1860",
    }

    tasks = load_tasks(args.tasks, args.limit)
    out = RESULTS_ROOT / args.tag.replace("/", "_")
    out.mkdir(parents=True, exist_ok=True)
    articles_path = out / "articles.jsonl"

    if args.recollect:
        return recollect_from_raw(args, out, articles_path)
    if args.score:
        return score_existing(args, out, articles_path)

    # 断点续跑：articles.jsonl 已有的题不重跑（中途崩溃后省 token）
    completed: list[dict] = []
    if articles_path.is_file():
        for line in articles_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                completed.append(json.loads(line))
    done_ids = {r["id"] for r in completed}
    if completed:
        print(f"续跑：已有 {len(completed)} 题（{sorted(done_ids)}），跳过")
    pending = [t for t in tasks if t["id"] not in done_ids]

    async def _run_pending() -> list[dict]:
        records: list[dict] = []
        for i, task in enumerate(pending, 1):
            sid = f"drb-{uuid.uuid4().hex}"
            print(f"[{i}/{len(pending)}] id={task['id']} session={sid} ...",
                  flush=True)
            t0 = time.perf_counter()
            run = await run_cli_agent(
                query=task["prompt"] + EVAL_MODE_SUFFIX,
                session_id=sid,
                user_id=eval_user_id,
                time_budget_seconds=args.time_budget,
                model=args.model_id,
                extra_env=subagent_env,
                raw_dump=out / "raw" / f"{task['id']}.jsonl",
            )
            record = {
                "id": task["id"],
                "topic": task["topic"],
                "language": task["language"],
                "prompt": task["prompt"],
                "article": run.get("final_text") or "",
                "session_id": sid,
                "elapsed_seconds": round(time.perf_counter() - t0, 1),
                "error": run.get("error"),
            }
            records.append(record)
            with articles_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(f"    article {len(record['article'])} 字，"
                  f"耗时 {record['elapsed_seconds']}s，error={record['error']}")
        return records

    with eval_langfuse_run(line="agent", tag=args.tag,
                           session_id=f"deepresearch-{args.tag}"):
        # 全批次共用一个事件循环：全局 pg_manager 连接池绑定首个 loop，
        # 逐题 asyncio.run 会跨 loop 崩溃
        new_records = asyncio.run(_run_pending())

    done = completed + new_records
    summary = {
        "benchmark": "deepresearch-bench",
        "tag": args.tag,
        "tasks": len(done),
        "task_ids": [r["id"] for r in done],
        "model_id": args.model_id,
        "eval_user": args.eval_user,
        "eval_user_id": eval_user_id,
        "mean_article_chars": round(
            sum(len(r["article"]) for r in done) / len(done)) if done else 0,
        "errors": sum(1 for r in done if r["error"]),
        "elapsed_seconds_total": round(sum(r["elapsed_seconds"] for r in done), 1),
        "upstream": "muset-ai/DeepResearch-Bench-Dataset (Apache-2.0)",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Results: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
