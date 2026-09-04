"""ERB 企业级检索评测（EnterpriseRAG-Bench 子集）：Recall@K / MRR / nDCG / 阈值校准 / 负拒率 / 消融。

语料：erb-eval 集合（312 GT + 220 confluence 干扰，短名规则见 ingest_plan.json）。
数据集：evals/kb/erb_data/（gitignored；ERB_DATA_DIR 可覆盖）。

用法（backend/ 下）:
    uv run python -m evals.kb.erb --sample 10              # 10 正 + 5 负 抽样
    uv run python -m evals.kb.erb --sample 10 --ablation   # 附消融（4 档管线配置对比）
    uv run python -m evals.kb.erb --all --tag baseline     # 全量 211 正 + 20 负

设计: 单次检索记录原始 rerank 分（score_threshold=0），阈值效果离线模拟，不重复调用 API。
产物: evals/kb/results/<tag>/{manifest.json, raw.json, summary.json, summary.md}；tag 复用被拒绝。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

KB_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = KB_ROOT.parents[1]
RESULTS_ROOT = KB_ROOT / "results"
COLLECTION = "erb-eval"
# 离线模拟的阈值档位（含生产在用的 0.05）
THRESHOLDS = [0.30, 0.20, 0.15, 0.10, 0.05, 0.03, 0.0]
KS = [1, 3, 5, 10]
SAMPLE_SEED = 11
BOOTSTRAP_SEED = 11
# 消融档位：label → KbRetrievalService.search 覆盖参数
ABLATIONS = {
    "vector（纯语义）": {"search_mode": "vector", "use_reranker": False},
    "bm25（纯关键词）": {"search_mode": "bm25", "use_reranker": False},
    "hybrid（无重排）": {"search_mode": "hybrid", "use_reranker": False},
    "hybrid+rerank+阈值（生产）": None,  # 集合默认
}


def _ensure_path() -> None:
    root = str(BACKEND_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def data_dir() -> Path:
    default = KB_ROOT / "erb_data"
    return Path(os.environ.get("ERB_DATA_DIR") or default)


def load_data() -> tuple[list[dict], dict[str, str]]:
    base = data_dir()
    questions = [json.loads(l) for l in (base / "questions.jsonl").open(encoding="utf-8")]
    plan = json.load((base / "ingest_plan.json").open(encoding="utf-8"))
    return questions, plan["dsid_map"]  # 文件名 -> dsid


def _hits_to_rows(hits: list) -> list[dict]:
    return [{"file": h.file_name, "score": round(h.rerank_score or h.score, 4)} for h in hits]


def _search_kwargs() -> dict:
    return {"collection_name": COLLECTION, "score_threshold": 0.0, "final_top_k": 10}


def load_eval_set(mode: str, sample_n: int) -> tuple[list[dict], list[dict], dict[str, str]]:
    """返回 (正样本, 负样本, dsid_map)。"""
    questions, name_to_dsid = load_data()
    corpus_dsids = set(name_to_dsid.values())
    positives = [q for q in questions
                 if q["question_type"] != "info_not_found"
                 and q.get("expected_doc_ids")
                 # 只评 GT 全部在语料内的题（其余题检索不到是正确行为）
                 and all(d in corpus_dsids for d in q["expected_doc_ids"])]
    negatives = [q for q in questions if q["question_type"] == "info_not_found"]

    if mode == "sample":
        rng = random.Random(SAMPLE_SEED)
        pos = rng.sample(positives, min(sample_n, len(positives)))
        neg = rng.sample(negatives, min(max(1, sample_n // 2), len(negatives))) if negatives else []
    else:
        pos, neg = positives, negatives
    return pos, neg, name_to_dsid


def build_summary(
    pos_runs: list[dict], neg_runs: list[dict], *, threshold_window: int
) -> dict:
    from evals.kb.metrics import (
        gt_survival,
        negative_rejection,
        ndcg_at_k,
        percentile,
        rank_metrics,
        score_distribution,
    )

    ranks = [p["gt_rank"] for p in pos_runs]
    summary: dict = {
        "n_positives": len(pos_runs),
        "n_negatives": len(neg_runs),
        "metrics": rank_metrics(ranks, KS),
        "ndcg@10": (
            statistics.mean(
                ndcg_at_k(p["rows"], p["dsid_map"], p["expected"]) for p in pos_runs
            )
            if pos_runs
            else 0.0
        ),
        "score_distribution": score_distribution(pos_runs),
        "threshold_window": threshold_window,
        "thresholds": [
            {
                "threshold": th,
                "gt_alive": gt_survival(pos_runs, th, window=threshold_window),
                "neg_rejected": negative_rejection(neg_runs, th),
            }
            for th in THRESHOLDS
        ],
    }
    latencies = [p.get("latency_ms") for p in pos_runs if p.get("latency_ms") is not None]
    if latencies:
        summary["latency_ms"] = {
            "p50": round(percentile(latencies, 0.50), 1),
            "p95": round(percentile(latencies, 0.95), 1),
        }
    by_type: dict[str, list[int | None]] = {}
    for p, r in zip(pos_runs, ranks):
        by_type.setdefault(p["type"], []).append(r)
    summary["recall@5_by_type"] = {
        t: {"hit": sum(1 for r in rs if r is not None and r <= 5), "n": len(rs)}
        for t, rs in sorted(by_type.items())
    }
    return summary


def render_summary_md(summary: dict) -> str:
    m = summary["metrics"]
    lines = [
        f"# ERB 检索评测 summary",
        "",
        f"- 正样本 {summary['n_positives']} / 负样本 {summary['n_negatives']}"
        f" / 阈值窗口 top-{summary['threshold_window']}",
        "",
        "| 指标 | 值 | 95% CI |",
        "|---|---:|---|",
    ]
    for k in KS:
        lo, hi = m[f"recall@{k}_ci95"]
        lines.append(f"| Recall@{k} | {m[f'recall@{k}']:.1%} | [{lo:.1%}, {hi:.1%}] |")
    lo, hi = m["mrr_ci95"]
    lines.append(f"| MRR | {m['mrr']:.3f} | [{lo:.3f}, {hi:.3f}] |")
    lines.append(f"| nDCG@10 | {summary['ndcg@10']:.3f} | - |")
    dist = summary["score_distribution"]
    if dist["gt_median"] is not None:
        lines.append(
            f"| GT 分数中位 / 噪音中位 | {dist['gt_median']:.3f} / {dist['noise_median']:.3f} | - |"
        )
    lines.extend(["", "## 阈值工作点（正样本 GT 存活 × 负样本拒答）", "",
                  "| threshold | GT 存活 | 负拒答 |", "|---:|---:|---:|"])
    for row in summary["thresholds"]:
        lines.append(
            f"| {row['threshold']:.2f} | {row['gt_alive']}/{summary['n_positives']}"
            f" | {row['neg_rejected']}/{summary['n_negatives']} |"
        )
    if "ablation" in summary:
        lines.extend(["", "## 消融（Recall@5）", "", "| 档位 | Recall@5 |", "|---|---:|"])
        for row in summary["ablation"]:
            lines.append(f"| {row['label']} | {row['recall@5']:.0%} ({row['hit']}/{row['n']}) |")
    if summary.get("recall@5_by_type"):
        lines.extend(["", "## 分题型 Recall@5", "", "| 题型 | 命中/题数 |", "|---|---|"])
        for t, row in summary["recall@5_by_type"].items():
            lines.append(f"| {t} | {row['hit']}/{row['n']} |")
    return "\n".join(lines) + "\n"


def print_summary(summary: dict) -> None:
    m = summary["metrics"]
    print("\n" + "=" * 62)
    print("汇总指标")
    print("=" * 62)
    n = m["n"]
    for k in KS:
        hit = round(m[f"recall@{k}"] * n)
        print(f"  Recall@{k:<2} {hit:>3}/{n}  {m[f'recall@{k}']:.0%}")
    print(f"  MRR    {m['mrr']:.3f}   (miss 计 0，分母 {n})")
    print(f"  nDCG@10 {summary['ndcg@10']:.3f}")
    dist = summary["score_distribution"]
    if dist["gt_median"] is not None:
        print(f"  分数分布: GT 中位 {dist['gt_median']:.3f} / 噪音中位 {dist['noise_median']:.3f}")
    if "latency_ms" in summary:
        print(f"  检索时延: p50 {summary['latency_ms']['p50']}ms / p95 {summary['latency_ms']['p95']}ms")

    print(f"\n  阈值工作点（正样本 top-{summary['threshold_window']} GT 存活 × 负样本拒答）:")
    print(f"    {'th':>5} {'GT存活':>8} {'负拒答':>8}")
    for row in summary["thresholds"]:
        print(f"    {row['threshold']:>5.2f} {row['gt_alive']:>5}/{n}"
              f" {row['neg_rejected']:>5}/{summary['n_negatives']}")

    if "ablation" in summary:
        print("\n消融：各管线档位的 Recall@5（同题对比）")
        for row in summary["ablation"]:
            print(f"  {row['label']:<26} Recall@5 = {row['hit']}/{row['n']}  ({row['recall@5']:.0%})")


async def run_ablation(pos_runs: list[dict]) -> list[dict]:
    from noesis.knowledge.retrieval.service import KbRetrievalService
    from evals.kb.metrics import gt_rank, recall_at_k

    results = []
    for label, overrides in ABLATIONS.items():
        kwargs = dict(_search_kwargs())
        if overrides:
            kwargs.update(overrides)
        ranks = []
        for p in pos_runs:
            res = await asyncio.to_thread(
                KbRetrievalService.search, query=p["question"], **kwargs)
            ranks.append(gt_rank(_hits_to_rows(res.hits), p["dsid_map"], p["expected"]))
        results.append({
            "label": label,
            "hit": sum(1 for r in ranks if r is not None and r <= 5),
            "n": len(ranks),
            "recall@5": recall_at_k(ranks, 5),
        })
    return results


async def run(mode: str, sample_n: int, ablation: bool, tag: str, threshold_window: int) -> None:
    import time

    from evals.kb.metrics import gt_rank
    from evals.langfuse_env import eval_langfuse_run
    from evals.manifest import build_manifest, init_results_dir, write_manifest
    from noesis.knowledge.retrieval.service import KbRetrievalService
    from noesis.knowledge.runtime import knowledge_base

    out_dir = init_results_dir(RESULTS_ROOT, tag)
    pos, neg, name_to_dsid = load_eval_set(mode, sample_n)
    print(f"评测集: 正样本 {len(pos)} 负样本 {len(neg)}；tag={tag} → {out_dir}")

    await knowledge_base.initialize()

    pos_runs: list[dict] = []
    neg_runs: list[dict] = []
    with eval_langfuse_run(line="kb", tag=tag, session_id=f"erb-{tag}"):
        for q in pos:
            started = time.perf_counter()
            res = KbRetrievalService.search(query=q["question"], **_search_kwargs())
            latency = (time.perf_counter() - started) * 1000
            rows = _hits_to_rows(res.hits)
            r = gt_rank(rows, name_to_dsid, q["expected_doc_ids"])
            print(f"\n=== {q['question_id']} [{q['question_type']}] GT rank={r if r else '未进top10'}")
            print(f"Q: {q['question'][:90]}")
            for i, h in enumerate(rows[:5], 1):
                mark = " ←GT" if name_to_dsid.get(h["file"]) in q["expected_doc_ids"] else ""
                print(f"  {i}. {h['score']:.4f}  {h['file'][:66]}{mark}")
            pos_runs.append({
                "question_id": q["question_id"], "question": q["question"],
                "type": q["question_type"], "rows": rows,
                "expected": q["expected_doc_ids"], "gt_rank": r,
                "latency_ms": round(latency, 1),
                "dsid_map": name_to_dsid,
            })

        for q in neg:
            res = KbRetrievalService.search(query=q["question"], **_search_kwargs())
            rows = _hits_to_rows(res.hits)
            neg_runs.append({"question_id": q["question_id"], "question": q["question"], "rows": rows})
            print(f"\n=== {q['question_id']} [info_not_found]"
                  f" top1={rows[0]['score'] if rows else 'n/a'}")

    summary = build_summary(pos_runs, neg_runs, threshold_window=threshold_window)
    if ablation and pos_runs:
        summary["ablation"] = await run_ablation(pos_runs)
    print_summary(summary)

    raw = {
        "positives": [{k: v for k, v in p.items() if k != "dsid_map"} for p in pos_runs],
        "negatives": neg_runs,
    }
    (out_dir / "raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "summary.md").write_text(render_summary_md(summary), encoding="utf-8")

    from noesis.config.env import ModelConfig
    write_manifest(out_dir, build_manifest(
        eval_line="kb", tag=tag,
        embedding_model=ModelConfig.embedding_model_name,
        rerank_model=ModelConfig.rerank_model_name,
        dataset={"path": str(data_dir()), "positives": len(pos), "negatives": len(neg),
                 "sample_seed": SAMPLE_SEED},
        config={"collection": COLLECTION, "thresholds": THRESHOLDS, "ks": KS,
                "threshold_window": threshold_window,
                "search_calls": len(pos_runs) + len(neg_runs),
                "bootstrap_seed": BOOTSTRAP_SEED},
        usage={"input_tokens": 0, "output_tokens": 0},
        notes="检索线无 chat token；embedding/rerank 调用次数见 config.search_calls",
    ))
    print(f"\nResults: {out_dir}")


def _auto_tag() -> str:
    return f"run-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    # 注意不能给 default：与传入值相同时 required 组会误判为未提供
    g.add_argument("--sample", type=int, help="正样本抽样条数（负样本取其一半）")
    g.add_argument("--all", action="store_true", help="全量正+负样本")
    ap.add_argument("--ablation", action="store_true", help="附加管线消融对比（4 档 × 正样本）")
    ap.add_argument("--tag", default=_auto_tag(), help="结果标签（results/<tag>/，复用被拒绝）")
    ap.add_argument("--threshold-window", type=int, default=10,
                    help="阈值模拟的 GT 存活窗口（默认 10，与 Recall 口径一致）")
    args = ap.parse_args()
    _ensure_path()
    asyncio.run(run("sample" if args.sample else "all", args.sample or 0,
                    args.ablation, args.tag, args.threshold_window))
