"""ERB 企业级检索评测（EnterpriseRAG-Bench 子集）：Recall@K / 阈值校准 / 负拒率。

语料：erb-eval 集合（312 GT + 220 confluence 干扰，短名规则见 ingest_plan.json）。
数据集：evals/kb/erb_data/（gitignored；ERB_DATA_DIR 可覆盖）。

用法（backend/ 下）:
    uv run python -m evals.kb.erb --sample 2   # 抽样冒烟（正/负各一）
    uv run python -m evals.kb.erb --all        # 全量 211 正 + 20 负

设计: 单次检索记录原始 rerank 分（score_threshold=0），阈值效果离线模拟，不重复调用 API。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from pathlib import Path

KB_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = KB_ROOT.parents[1]
COLLECTION = "erb-eval"
# 离线模拟的阈值档位（含生产在用的 0.05）
THRESHOLDS = [0.30, 0.20, 0.15, 0.10, 0.05, 0.03, 0.0]


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


def eval_positive(q: dict, hits: list[dict], dsids: list) -> None:
    gt_ranks = [i for i, d in enumerate(dsids, 1) if d in q["expected_doc_ids"]]
    rank1 = gt_ranks[0] if gt_ranks else None
    print(f"  GT 首个命中排名: {rank1}  top5含GT: {bool(gt_ranks) and gt_ranks[0] <= 5}")
    if q.get("gold_answer"):
        print(f"  标准答案: {q['gold_answer'][:120]}")


def eval_negative(hits: list[dict]) -> None:
    # 负样本：在用阈值下应无结果（拒答）
    for th in THRESHOLDS:
        kept = sum(1 for h in hits if h["score"] >= th)
        print(f"  th={th:.2f}: 剩余 {kept} 条" + ("  ✓ 拒答" if kept == 0 else "  ✗ 误检"))


async def run(mode: str) -> None:
    from noesis.knowledge.retrieval.service import KbRetrievalService
    from noesis.knowledge.runtime import knowledge_base

    questions, name_to_dsid = load_data()
    corpus_dsids = set(name_to_dsid.values())
    positives = [q for q in questions
                 if q["question_type"] != "info_not_found"
                 and q.get("expected_doc_ids")
                 # 只评 GT 全部在语料内的题（其余题检索不到是正确行为）
                 and all(d in corpus_dsids for d in q["expected_doc_ids"])]
    negatives = [q for q in questions if q["question_type"] == "info_not_found"]

    if mode == "sample":
        rng = random.Random(7)
        sampled = [rng.choice(positives), rng.choice(negatives)]
    else:
        sampled = positives + negatives
    print(f"评测集: 正样本 {len(positives)} 负样本 {len(negatives)}，本次运行 {len(sampled)} 题")

    await knowledge_base.initialize()

    for q in sampled:
        res = KbRetrievalService.search(
            collection_name=COLLECTION,
            query=q["question"],
            # 关闭阈值记录原始分；rerank/final 配置跟随集合默认
            score_threshold=0.0,
            final_top_k=10,
        )
        hits = _hits_to_rows(res.hits)
        dsids = [name_to_dsid.get(h["file"]) for h in hits]
        print(f"\n=== {q['question_id']} [{q['question_type']}] gt={q.get('expected_doc_ids')}")
        print(f"Q: {q['question'][:100]}")
        for i, (h, d) in enumerate(zip(hits, dsids), 1):
            gt_mark = " ←GT" if q.get("expected_doc_ids") and d in q["expected_doc_ids"] else ""
            print(f"  {i:>2}. {h['score']:.4f}  {h['file'][:70]}{gt_mark}")

        if q["question_type"] == "info_not_found":
            eval_negative(hits)
        else:
            eval_positive(q, hits, dsids)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    # 注意不能给 default：与传入值相同时 required 组会误判为未提供
    g.add_argument("--sample", type=int, help="抽样冒烟条数")
    g.add_argument("--all", action="store_true", help="全量正+负样本")
    args = ap.parse_args()
    _ensure_path()
    asyncio.run(run("sample" if args.sample else "all"))
