"""ERB 端到端评测（CLI 驱动 + RAGAS 判卷）：答题与判卷两阶段，断点续跑。

用法（backend/ 下）:
    uv run --with ragas --with langchain-openai python -m evals.agent.rag.ragas_e2e \
        --tag smoke5 --positives 5 --negatives 2
    uv run --with ragas --with langchain-openai python -m evals.agent.rag.ragas_e2e \
        --tag full-100 --positives 80 --negatives 20

指标口径:
- 金标事实覆盖率（正样本）: 金标 claims ⊢ 回答 的 NLI 比例（ERB Completeness 语义;
  RAGAS FactualCorrectness 的拆解/NLI 基建, 单向公式——归因措辞免疫, 见
  results/smoke-cli-ragas-01/fc-root-cause.md）
- Faithfulness: RAGAS 原生（回答 claims ⊢ 检索上下文, 防编造）
- 引用合规率（正样本）: 格式合规 且 引用文档 ⊆ 期望出处（确定性, 产品契约）
- 端到端拒答率（负样本）: 拒答判定标准（answer_facts[0]）⊢ 回答 的 NLI 判定
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import signal
import statistics
import time
import uuid
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT.parents[2]
RESULTS_ROOT = ROOT / "results"
EVAL_ENV_FILE = BACKEND / "evals" / ".env"
DATASET = ROOT / "fixtures" / "erb211.jsonl"
KB_QUESTIONS = BACKEND / "evals" / "kb" / "erb_data" / "questions.jsonl"
COLLECTION = "erb-eval"
EVAL_USER_ID = "00000000-0000-4000-8000-0000000000ea"
SAMPLE_SEED = 11
ANSWER_TIMEOUT_S = 300


def load_subjects(positives_n: int, negatives_n: int, seed: int) -> list[dict]:
    rows = [json.loads(l) for l in DATASET.read_text(encoding="utf-8").splitlines() if l.strip()]
    rng = random.Random(seed)
    pos = rng.sample(rows, min(positives_n, len(rows)))
    subjects = [
        {
            "question_id": r["id"], "negative": False, "query": r["query"],
            "gold_answer": r.get("gold_answer") or "",
            "expected_sources": r.get("expected_sources") or [],
        }
        for r in pos
    ]
    if negatives_n:
        kb = [json.loads(l) for l in KB_QUESTIONS.read_text(encoding="utf-8").splitlines() if l.strip()]
        neg = [r for r in kb if r.get("question_type") == "info_not_found"][:negatives_n]
        subjects += [
            {
                "question_id": r["question_id"], "negative": True, "query": r["question"],
                "gold_answer": "", "expected_sources": [],
                "refusal_criterion": (r.get("answer_facts") or [""])[0],
            }
            for r in neg
        ]
    return subjects


def eval_env() -> dict[str, str]:
    raw = dotenv_values(EVAL_ENV_FILE)
    env = dict(os.environ)
    for key in ("NOESIS_API_KEY", "NOESIS_BASE_URL", "NOESIS_MODEL"):
        value = os.environ.get(key) or str(raw.get(key) or "")
        if value.strip():
            env[key] = value.strip()
    env.update(
        NOESIS_USER_ID=EVAL_USER_ID,
        REQUEST_TIMEOUT="600",
        STREAM_IDLE_TIMEOUT="300",
        DB_ECHO="false",
    )
    return env


async def run_answer(subject: dict, env: dict[str, str]) -> dict:
    """跑一题：spawn CLI json 模式子进程，返回落盘记录。"""
    session_id = f"re2e-{uuid.uuid4().hex[:16]}"[:36]
    argv = [
        "uv", "run", "noesis", "chat", "-p", subject["query"],
        "-t", "common", "--output-format", "json",
        "--session-id", session_id,
        "--kb-collections", COLLECTION, "--no-web-search",
    ]
    t0 = time.perf_counter()
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(BACKEND), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=ANSWER_TIMEOUT_S)
    except asyncio.TimeoutError:
        _kill_group(proc)
        return {**subject, "completed": False, "error": f"timeout after {ANSWER_TIMEOUT_S}s"}
    latency_ms = int((time.perf_counter() - t0) * 1000)
    record = {
        **subject, "session_id": session_id, "latency_ms": latency_ms,
        "cli_exit_code": proc.returncode,
        "stderr_tail": stderr.decode("utf-8", "replace")[-2000:],
    }
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        record.update(completed=False, error=f"unparseable stdout (exit {proc.returncode})")
        return record
    record.update(
        completed=bool(payload.get("completed")) and not payload.get("error"),
        error=payload.get("error"),
        final_text=payload.get("final_text") or "",
        tool_stats=payload.get("tool_stats") or {},
        tool_outputs=payload.get("tool_outputs") or [],
        input_tokens=payload.get("input_tokens") or 0,
        output_tokens=payload.get("output_tokens") or 0,
        model=payload.get("model"),
    )
    return record


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def contexts_from_record(record: dict) -> list[str]:
    from evals.agent.citation import _collect_excerpts

    excerpts = _collect_excerpts(record.get("tool_outputs") or [])
    return list(excerpts.values())


async def _retry(factory, attempts: int = 3):
    """判卷调用重试：裁判偶发超长输出被网关截断（finish_reason=length →
    LLMDidNotFinish），属瞬态失败，重试通常可过。"""
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return await factory()
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise last  # type: ignore[misc]


async def answer_phase(subjects: list[dict], out_dir: Path, concurrency: int) -> list[dict]:
    answers_dir = out_dir / "answers"
    answers_dir.mkdir(parents=True, exist_ok=True)
    env = eval_env()
    sem = asyncio.Semaphore(concurrency)
    done: dict[str, dict] = {}

    async def one(subject: dict) -> None:
        qid = subject["question_id"]
        path = answers_dir / f"{qid}.json"
        if path.is_file():
            done[qid] = json.loads(path.read_text(encoding="utf-8"))
            return
        async with sem:
            record = await run_answer(subject, env)
            if not record.get("completed"):  # 失败重试一次（网络抖动等瞬态故障）
                retry = await run_answer(subject, env)
                if retry.get("completed"):
                    record = retry
                else:
                    record["retry_error"] = retry.get("error")
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        done[qid] = record
        status = "ok" if record.get("completed") else f"FAIL: {str(record.get('error'))[:60]}"
        print(f"[answer] {qid} {status}", flush=True)

    await asyncio.gather(*(one(s) for s in subjects))
    return [done[s["question_id"]] for s in subjects]


async def judge_phase(records: list[dict], out_dir: Path, concurrency: int) -> list[dict]:
    from dotenv import dotenv_values

    from evals.agent.citation import citation_metrics
    from langchain_openai import ChatOpenAI
    from ragas.dataset_schema import SingleTurnSample
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import FactualCorrectness, Faithfulness

    raw = dotenv_values(EVAL_ENV_FILE)
    env = dict(os.environ)
    for key in ("NOESIS_API_KEY", "NOESIS_BASE_URL"):
        value = os.environ.get(key) or str(raw.get(key) or "")
        if value.strip():
            env[key] = value.strip()

    judge_llm = LangchainLLMWrapper(ChatOpenAI(
        model="deepseek-v4-flash", base_url=env["NOESIS_BASE_URL"],
        api_key=env["NOESIS_API_KEY"], temperature=0, timeout=300, max_retries=1,
        max_tokens=16000,
    ))
    fc = FactualCorrectness(mode="recall")
    fc.llm = judge_llm
    faith = Faithfulness(llm=judge_llm)

    judged_dir = out_dir / "judged"
    judged_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    results: list[dict] = []

    async def one(record: dict) -> None:
        qid = record["question_id"]
        path = judged_dir / f"{qid}.json"
        if path.is_file():
            results.append(json.loads(path.read_text(encoding="utf-8")))
            return
        judged = {"question_id": qid, "negative": record.get("negative", False)}
        try:
            if not record.get("final_text"):
                judged["skip_reason"] = "no final_text"
            else:
                async with sem:
                    if record.get("negative"):
                        verdicts = await _retry(lambda: fc.verify_claims(
                            premise=record["final_text"],
                            hypothesis_list=[record["refusal_criterion"]],
                            callbacks=None,
                        ))
                        judged["refusal"] = bool(verdicts[0]) if len(verdicts) else None
                    else:
                        gold_claims = await _retry(lambda: fc.decompose_claims(
                            record["gold_answer"], callbacks=None))
                        verdicts = await _retry(lambda: fc.verify_claims(
                            premise=record["final_text"],
                            hypothesis_list=gold_claims, callbacks=None,
                        ))
                        judged["coverage"] = (
                            round(float(sum(verdicts)) / len(verdicts), 4)
                            if len(verdicts) else None)
                        judged["gold_claims_n"] = len(gold_claims)
                        cit = citation_metrics(
                            record["final_text"],
                            expected_doc_files=record.get("expected_sources") or [],
                        )
                        cited = set(cit.get("cited_kb_files") or [])
                        expected = set(cit.get("expected_files") or [])
                        judged["citation_compliant"] = bool(
                            cit.get("format_compliant") and cited and cited <= expected)
                contexts = contexts_from_record(record)
                if contexts:
                    async with sem:
                        sample = SingleTurnSample(
                            user_input=record["query"],
                            response=record["final_text"], retrieved_contexts=contexts)
                        score = await _retry(
                            lambda: faith.single_turn_ascore(sample))
                        judged["faithfulness"] = round(float(score), 4)
                else:
                    judged["faithfulness"] = None
                    judged["no_contexts"] = True
        except Exception as exc:  # noqa: BLE001
            judged["judge_error"] = f"{type(exc).__name__}: {exc}"
        path.write_text(json.dumps(judged, ensure_ascii=False), encoding="utf-8")
        results.append(judged)
        print(f"[judge] {qid} {judged}", flush=True)

    await asyncio.gather(*(one(r) for r in records))
    return results


def summarize(records: list[dict], judged: list[dict], tag: str) -> dict:
    pos_rec = [r for r in records if not r.get("negative")]
    neg_rec = [r for r in records if r.get("negative")]
    by_qid = {j["question_id"]: j for j in judged}

    def mean_or_none(values: list) -> float | None:
        values = [v for v in values if v is not None]
        return round(statistics.fmean(values), 4) if values else None

    pos_judged = [by_qid[r["question_id"]] for r in pos_rec]
    neg_judged = [by_qid[r["question_id"]] for r in neg_rec]
    completed_pos = [r for r in pos_rec if r.get("completed")]

    def _n(items: list, key: str) -> int:
        return sum(1 for j in items if j.get(key) is not None)

    summary = {
        "tag": tag,
        "samples": {
            "positives": len(pos_rec), "negatives": len(neg_rec),
            "completed": sum(1 for r in records if r.get("completed")),
            "errors": sum(1 for r in records if not r.get("completed")),
        },
        "positives": {
            "mean_coverage": mean_or_none([j.get("coverage") for j in pos_judged]),
            "coverage_n": _n(pos_judged, "coverage"),
            "mean_faithfulness": mean_or_none([j.get("faithfulness") for j in pos_judged]),
            "faithfulness_n": _n(pos_judged, "faithfulness"),
            "citation_compliance_rate": round(
                sum(1 for j in pos_judged if j.get("citation_compliant"))
                / _n(pos_judged, "citation_compliant"), 4
            ) if _n(pos_judged, "citation_compliant") else None,
            "citation_n": _n(pos_judged, "citation_compliant"),
        },
        "negatives": {
            "refusal_rate": round(
                sum(1 for j in neg_judged if j.get("refusal"))
                / _n(neg_judged, "refusal"), 4
            ) if _n(neg_judged, "refusal") else None,
            "refusal_n": _n(neg_judged, "refusal"),
            "mean_faithfulness": mean_or_none([j.get("faithfulness") for j in neg_judged]),
            "faithfulness_n": _n(neg_judged, "faithfulness"),
        },
        "health": {
            "mean_latency_ms": mean_or_none([r.get("latency_ms") for r in completed_pos]),
            "mean_input_tokens": mean_or_none([r.get("input_tokens") for r in completed_pos]),
            "mean_output_tokens": mean_or_none([r.get("output_tokens") for r in completed_pos]),
            "judge_errors": sum(1 for j in judged if j.get("judge_error")),
        },
    }
    return summary


def render_summary_md(summary: dict, records: list[dict], judged: list[dict]) -> str:
    s, p, n, h = summary, summary["positives"], summary["negatives"], summary["health"]
    pct = lambda v: f"{v:.1%}" if isinstance(v, (int, float)) else "—"
    model = next((r.get("model") for r in records if r.get("model")), "glm-5.3-flash")
    lines = [
        f"# ERB 端到端评测 summary（{s['tag']}）",
        "",
        f"- 样本 {s['samples']['positives']} 正 + {s['samples']['negatives']} 负"
        f"（完成 {s['samples']['completed']} / error {s['samples']['errors']}）",
        f"- 被测 {model}（CLI 驱动，KB=erb-eval，web 搜索关闭）；裁判 deepseek-v4-flash",
        "",
        "| 指标 | 值（分母） |",
        "|---|---:|",
        f"| 金标事实覆盖率（正样本均值） | {pct(p['mean_coverage'])}（n={p['coverage_n']}/{s['samples']['positives']}） |",
        f"| Faithfulness（正样本均值） | {pct(p['mean_faithfulness'])}（n={p['faithfulness_n']}/{s['samples']['positives']}） |",
        f"| 引用合规率（正样本） | {pct(p['citation_compliance_rate'])}（n={p['citation_n']}/{s['samples']['positives']}） |",
        f"| 端到端拒答率（负样本） | {pct(n['refusal_rate'])}（n={n['refusal_n']}/{s['samples']['negatives']}） |",
        f"| 负样本 Faithfulness | {pct(n['mean_faithfulness'])}（n={n['faithfulness_n']}/{s['samples']['negatives']}） |",
        "",
        f"健康度：判卷失败 {h['judge_errors']} 题；正样本均值延迟 "
        f"{(h['mean_latency_ms'] or 0) / 1000:.0f}s；"
        f"tokens ↑{h['mean_input_tokens'] or 0} ↓{h['mean_output_tokens'] or 0}",
    ]
    return "\n".join(lines) + "\n"


async def _run(args: argparse.Namespace) -> int:
    out_dir = RESULTS_ROOT / args.tag
    if out_dir.exists() and not (out_dir / "answers").is_dir():
        raise SystemExit(f"结果目录已存在且非本脚本产物: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    subjects = load_subjects(args.positives, args.negatives, args.seed)
    (out_dir / "subjects.json").write_text(
        json.dumps(subjects, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"== 答题阶段：{len(subjects)} 题，并发 {args.concurrency} ==")
    records = await answer_phase(subjects, out_dir, args.concurrency)
    print(f"== 判卷阶段：并发 {args.judge_concurrency} ==")
    judged = await judge_phase(records, out_dir, args.judge_concurrency)

    summary = summarize(records, judged, args.tag)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "summary.md").write_text(render_summary_md(summary, records, judged), encoding="utf-8")
    print("\n" + render_summary_md(summary, records, judged))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--positives", type=int, default=80)
    ap.add_argument("--negatives", type=int, default=20)
    ap.add_argument("--seed", type=int, default=SAMPLE_SEED)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--judge-concurrency", type=int, default=6)
    raise SystemExit(asyncio.run(_run(ap.parse_args())))
