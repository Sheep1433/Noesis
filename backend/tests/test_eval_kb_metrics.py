"""kb 指标纯函数测试：MRR 分母、nDCG 多 GT、阈值窗口、bootstrap CI。"""

import pytest

from evals.kb.metrics import (
    bootstrap_ci,
    gt_rank,
    gt_survival,
    mrr,
    negative_rejection,
    ndcg_at_k,
    percentile,
    rank_metrics,
    recall_at_k,
    score_distribution,
)

DSID = {"a.md": "A", "b.md": "B", "c.md": "C"}


def rows(*pairs):
    return [{"file": f, "score": s} for f, s in pairs]


def test_gt_rank_first_and_miss():
    r = rows(("a.md", 0.9), ("b.md", 0.5))
    assert gt_rank(r, DSID, ["A"]) == 1
    assert gt_rank(r, DSID, ["B"]) == 2
    assert gt_rank(r, DSID, ["Z"]) is None


def test_mrr_counts_miss_as_zero():
    # 3 题：rank1、rank2、miss → (1 + 0.5 + 0) / 3
    assert mrr([1, 2, None]) == pytest.approx(0.5)
    # 全命中 rank1 → 1.0
    assert mrr([1, 1]) == 1.0
    assert mrr([]) == 0.0


def test_recall_at_k_miss_in_denominator():
    ranks = [1, 3, None]
    assert recall_at_k(ranks, 1) == pytest.approx(1 / 3)
    assert recall_at_k(ranks, 3) == pytest.approx(2 / 3)


def test_ndcg_multi_gt_ideal():
    # 两个 GT 都排前二 → nDCG = 1
    r = rows(("a.md", 0.9), ("b.md", 0.8), ("c.md", 0.1))
    assert ndcg_at_k(r, DSID, ["A", "B"]) == pytest.approx(1.0)


def test_ndcg_gt_below_noise():
    # GT 排第 2（噪音在前）：DCG = 1/log2(3)，IDCG = 1
    r = rows(("c.md", 0.9), ("a.md", 0.8))
    assert ndcg_at_k(r, DSID, ["A"]) == pytest.approx(1 / 1.58496, abs=1e-4)


def test_ndcg_multi_gt_partial():
    # 2 个 GT 只命中 1 个且在第 2 位
    r = rows(("c.md", 0.9), ("a.md", 0.8))
    dcg = 1 / 1.58496
    idcg = 1 + 1 / 1.58496
    assert ndcg_at_k(r, DSID, ["A", "B"]) == pytest.approx(dcg / idcg, abs=1e-4)


def test_ndcg_no_gt_returns_zero():
    assert ndcg_at_k(rows(("c.md", 0.9)), DSID, []) == 0.0


def test_ndcg_same_doc_multiple_chunks_counted_once():
    # 同一 GT 文档两个 chunk 占据前两位：只按首个计分，nDCG 不得超过 1
    r = rows(("a.md", 0.9), ("a.md", 0.8), ("c.md", 0.1))
    assert ndcg_at_k(r, DSID, ["A"]) == pytest.approx(1.0)


def test_percentile_monotone_small_samples():
    values = [697.9, 7431.3]
    assert percentile(values, 0.50) == pytest.approx(697.9)  # nearest-rank：两样本取下位
    assert percentile(values, 0.95) == pytest.approx(7431.3)
    assert percentile(values, 0.95) >= percentile(values, 0.50)


def test_gt_survival_respects_window_and_threshold():
    positives = [{
        "rows": rows(("c.md", 0.4), ("a.md", 0.3), ("a2.md", 0.2)),
        "dsid_map": {**DSID, "a2.md": "A2"},
        "expected": ["A"],
    }]
    # GT 在第 2 位：窗口 2 且阈值 0.25 → 存活
    assert gt_survival(positives, 0.25, window=2) == 1
    # 窗口 1（只看第 1 位）→ 不存活
    assert gt_survival(positives, 0.25, window=1) == 0
    # 阈值高于 GT 分 → 不存活
    assert gt_survival(positives, 0.35, window=2) == 0


def test_negative_rejection():
    negs = [{"rows": rows(("c.md", 0.12))}, {"rows": rows(("c.md", 0.4))}]
    assert negative_rejection(negs, 0.3) == 1
    assert negative_rejection(negs, 0.05) == 0


def test_score_distribution_medians():
    positives = [{
        "rows": rows(("a.md", 0.5), ("c.md", 0.1)),
        "dsid_map": DSID,
        "expected": ["A"],
    }]
    dist = score_distribution(positives)
    assert dist["gt_median"] == 0.5
    assert dist["noise_median"] == 0.1


def test_bootstrap_ci_deterministic_and_brackets_mean():
    values = [1.0] * 7 + [0.0] * 3
    lo1, hi1 = bootstrap_ci(values, seed=11)
    lo2, hi2 = bootstrap_ci(values, seed=11)
    assert (lo1, hi1) == (lo2, hi2)
    assert lo1 <= 0.7 <= hi1
    assert 0.0 <= lo1 <= hi1 <= 1.0


def test_rank_metrics_shape():
    out = rank_metrics([1, None], ks=[1, 3])
    assert out["n"] == 2
    assert out["recall@1"] == 0.5
    assert out["mrr"] == 0.5
    lo, hi = out["recall@1_ci95"]
    assert 0.0 <= lo <= hi <= 1.0
