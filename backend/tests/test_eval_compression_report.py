"""压缩评测 report 测试：recall% 聚合、解析失败剔除、任务保持率 Δ。"""

import json

import pytest

from evals.compression.report import (
    build_summary,
    summarize_arm_runs,
    write_summary,
)


def _arm_run(fixture_id: str, arm: str, recalls: list[int | None]) -> dict:
    return {
        "fixture_id": fixture_id,
        "arm": arm,
        "policy": None if arm == "uncompacted" else arm,
        "compression": {"pre_tokens": 1000, "post_tokens": 200 if arm != "uncompacted" else 1000,
                        "compression_ratio": 0.8},
        "probes": [
            {
                "probe_id": f"p{i}",
                "type": "recall",
                "recall": r,
                "scores": {d: 4 for d in
                           ("accuracy", "artifact_trail", "context_awareness", "continuity", "completeness")},
                "overall_probe_score": 4.0,
            }
            for i, r in enumerate(recalls)
        ],
    }


def test_summarize_arm_runs_recall_and_parse_error_exclusion():
    runs = [_arm_run("f1", "compressed:current", [2, 1, None])]
    summary = summarize_arm_runs(runs)
    # (2+1) / (2×2 有效题) = 0.75；解析失败题剔除分母
    assert summary["recall_pct"] == 0.75
    assert summary["judge_parse_error_rate"] == pytest.approx(1 / 3, abs=1e-3)
    assert summary["retained_tokens"] == 200


def test_summarize_arm_runs_median_over_runs():
    runs = [
        _arm_run("f1", "compressed:current", [2, 2]),
        _arm_run("f1", "compressed:current", [0, 0]),
    ]
    assert summarize_arm_runs(runs)["recall_pct"] == 0.5


def test_build_summary_retention_delta_per_fixture():
    rows = [
        summarize_arm_runs([_arm_run("f1", "uncompacted", [2, 2, 2, 2])]),
        summarize_arm_runs([_arm_run("f1", "compressed:current", [2, 1, 2, 0])]),
    ]
    summary = build_summary("t", rows)
    compressed = next(r for r in summary["fixtures"] if r["arm"] == "compressed:current")
    # uncompacted 100% vs compressed 62.5% → Δ = -37.5pt
    assert compressed["retention_delta"] == pytest.approx(0.625 - 1.0)
    assert summary["arms"]["uncompacted"]["recall_pct"] == 1.0
    assert summary["arms"]["compressed:current"]["recall_pct"] == pytest.approx(0.625)


def test_write_summary_md_contains_headline(tmp_path, monkeypatch):
    rows = [
        summarize_arm_runs([_arm_run("f1", "uncompacted", [2, 2])]),
        summarize_arm_runs([_arm_run("f1", "compressed:current", [1, 0])]),
    ]
    summary = build_summary("tag-x", rows)
    monkeypatch.setattr("evals.compression.report.RESULTS_ROOT", tmp_path)
    json_path, md_path = write_summary("tag-x", summary)
    md = md_path.read_text(encoding="utf-8")
    assert "recall% @ retained tokens" in md
    assert "uncompacted" in md and "compressed:current" in md
    assert json.loads(json_path.read_text(encoding="utf-8"))["tag"] == "tag-x"
