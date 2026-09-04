"""ERB 检索评测指标纯函数：Recall@K / MRR（miss 计 0）/ nDCG@10 / 阈值模拟 / bootstrap CI。

输入约定：rows = [{"file": 名, "score": 原始 rerank 分}]（按排名序），
expected = dsid 集合，dsid_map = 文件名 → dsid。全部无副作用，供 erb.py 与单测共用。
"""

from __future__ import annotations

import math
import random
import statistics
from typing import Any, Callable, Sequence

Rows = Sequence[dict[str, Any]]


def gt_rank(rows: Rows, dsid_map: dict[str, str], expected: Sequence[str]) -> int | None:
    """GT 首个命中排名（1-based）；未命中返回 None。"""
    for i, h in enumerate(rows, 1):
        if dsid_map.get(h["file"]) in expected:
            return i
    return None


def recall_at_k(ranks: Sequence[int | None], k: int) -> float:
    if not ranks:
        return 0.0
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks)


def mrr(ranks: Sequence[int | None]) -> float:
    """MRR：未命中题贡献 0、分母为全部题数（剔除 miss 会虚高）。"""
    if not ranks:
        return 0.0
    return sum(1 / r for r in ranks if r is not None) / len(ranks)


def ndcg_at_k(rows: Rows, dsid_map: dict[str, str], expected: Sequence[str], k: int = 10) -> float:
    """nDCG@k，多 GT 文档按二值相关折算；同一文档多 chunk 只按首次命中计一次。"""
    ideal_hits = min(len(set(expected)), k)
    if ideal_hits == 0:
        return 0.0
    seen: set[str] = set()
    dcg = 0.0
    for i, h in enumerate(rows[:k], 1):
        dsid = dsid_map.get(h["file"])
        if dsid in expected and dsid not in seen:
            seen.add(dsid)
            dcg += 1.0 / math.log2(i + 1)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


def gt_survival(
    positives: Sequence[dict[str, Any]], threshold: float, *, window: int = 10
) -> int:
    """阈值下正样本 GT 存活数：top-window 内存在分数 ≥ 阈值的 GT 文档。"""
    return sum(
        1
        for p in positives
        if any(
            h["score"] >= threshold and p["dsid_map"].get(h["file"]) in p["expected"]
            for h in p["rows"][:window]
        )
    )


def negative_rejection(negatives: Sequence[dict[str, Any]], threshold: float) -> int:
    """阈值下负样本拒答数：top-10 无任何分数 ≥ 阈值的命中。"""
    return sum(1 for neg in negatives if not any(h["score"] >= threshold for h in neg["rows"]))


def score_distribution(positives: Sequence[dict[str, Any]]) -> dict[str, float | None]:
    """GT 命中分数 vs 噪音分数的中位数（阈值校准依据）。"""
    gt_scores = [
        h["score"]
        for p in positives
        for h in p["rows"]
        if p["dsid_map"].get(h["file"]) in p["expected"]
    ]
    noise_scores = [
        h["score"]
        for p in positives
        for h in p["rows"]
        if p["dsid_map"].get(h["file"]) not in p["expected"]
    ]
    return {
        "gt_median": statistics.median(gt_scores) if gt_scores else None,
        "noise_median": statistics.median(noise_scores) if noise_scores else None,
    }


def percentile(values: Sequence[float], q: float) -> float:
    """nearest-rank 分位数；与 median 保持单调（小样本下 p95 不会低于 p50）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered), math.ceil(q * len(ordered))) - 1)
    return ordered[idx]


def bootstrap_ci(
    values: Sequence[float],
    stat: Callable[[Sequence[float]], float] = lambda xs: sum(xs) / len(xs),
    *,
    n_boot: int = 1000,
    seed: int = 11,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """按题重采样的 percentile 置信区间；values 为每题一个统计量（miss=0 已计入）。"""
    if not values:
        return 0.0, 0.0
    rng = random.Random(seed)
    n = len(values)
    stats = sorted(
        stat([values[rng.randrange(n)] for _ in range(n)]) for _ in range(n_boot)
    )
    lo_idx = int(n_boot * alpha / 2)
    hi_idx = max(lo_idx + 1, int(n_boot * (1 - alpha / 2)))
    return stats[lo_idx], stats[min(hi_idx, n_boot) - 1]


def rank_metrics(ranks: Sequence[int | None], ks: Sequence[int]) -> dict[str, Any]:
    """一次性算出 Recall@K / MRR 与各自 bootstrap 95% CI。"""
    out: dict[str, Any] = {"n": len(ranks)}
    for k in ks:
        values = [1.0 if r is not None and r <= k else 0.0 for r in ranks]
        out[f"recall@{k}"] = recall_at_k(ranks, k)
        out[f"recall@{k}_ci95"] = bootstrap_ci(values)
    rr_values = [1.0 / r if r is not None else 0.0 for r in ranks]
    out["mrr"] = mrr(ranks)
    out["mrr_ci95"] = bootstrap_ci(rr_values)
    return out
