"""压缩评测 report 测试：recall% 聚合、解析失败剔除、任务保持率 Δ、检索兜底收益。"""

import json

import pytest

from evals.compression.report import (
    RECOVERY,
    attach_recovery_gains,
    build_summary,
    summarize_arm_runs,
    write_summary,
)


def _arm_row(fixture_id: str, arm: str, recall_pct: float) -> dict:
    """直接构造 summarize_arm_runs 形状的行（检索收益配对用）。"""
    return {
        "fixture_id": fixture_id,
        "arm": arm,
        "policy": None if arm == "uncompacted" else arm,
        "runs": 1,
        "recall_pct": recall_pct,
        "judge_parse_error_rate": 0.0,
        "retained_tokens": 100,
        "pre_tokens": 1000,
        "compression_ratio": 0.9,
        "dimension_medians": {},
        "recall_by_layer": {},
        "probes": [],
    }


def _arm_run(
    fixture_id: str,
    arm: str,
    recalls: list[int | None],
    layers: list[str | None] | None = None,
) -> dict:
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
                "layer": layers[i] if layers else None,
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


def test_summarize_arm_runs_recall_by_layer():
    # macro 2 题 2/2 分、meso 2 题 1/1 分、detail 2 题 0/0 分；
    # 无 layer 标注的旧题不计入任何层
    runs = [_arm_run("f1", "compressed:current", [2, 2, 1, 1, 0, 0, 2],
                     layers=["macro", "macro", "meso", "meso",
                             "detail", "detail", None])]
    summary = summarize_arm_runs(runs)
    assert summary["recall_by_layer"] == {
        "macro": 1.0, "meso": 0.5, "detail": 0.0,
    }


def test_summarize_arm_runs_without_layers_has_empty_by_layer():
    summary = summarize_arm_runs([_arm_run("f1", "current", [2, 0])])
    assert summary["recall_by_layer"] == {}


def test_build_summary_arm_level_recall_by_layer_and_md_section(tmp_path, monkeypatch):
    rows = [
        summarize_arm_runs([_arm_run("f1", "current", [2, 2, 0, 0],
                                     layers=["macro", "macro", "detail", "detail"])]),
        summarize_arm_runs([_arm_run("f1", "recovery", [2, 2, 2, 2],
                                     layers=["macro", "macro", "detail", "detail"])]),
    ]
    summary = build_summary("tag-layer", rows)
    assert summary["arms"]["current"]["recall_by_layer"] == {
        "macro": 1.0, "detail": 0.0,
    }
    monkeypatch.setattr("evals.compression.report.RESULTS_ROOT", tmp_path)
    _, md_path = write_summary("tag-layer", summary)
    md = md_path.read_text(encoding="utf-8")
    assert "分层召回" in md
    assert "宏观" in md and "微观" in md
    # v1 无分层题库：不渲染分层节
    rows_v1 = [
        summarize_arm_runs([_arm_run("f1", "current", [2, 0])]),
    ]
    summary_v1 = build_summary("tag-v1", rows_v1)
    _, md_v1 = write_summary("tag-v1", summary_v1)
    assert "分层召回" not in md_v1.read_text(encoding="utf-8")


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


def test_attach_recovery_gains_pairs_recovery_with_closed_book():
    rows = [
        _arm_row("f1", "uncompacted", 0.9),
        _arm_row("f1", "current", 0.4),
        _arm_row("f1", RECOVERY, 0.68),
        _arm_row("f2", "current", 0.5),  # 无 recovery 行：不算收益
    ]
    out = attach_recovery_gains(rows)
    by_key = {(r["fixture_id"], r["arm"]): r for r in out}
    assert by_key[("f1", RECOVERY)]["recovery_gain"] == pytest.approx(0.28)
    assert "recovery_gain" not in by_key[("f2", "current")]


def test_build_summary_reports_recovery_gain_headline(tmp_path, monkeypatch):
    monkeypatch.setattr("evals.compression.report.RESULTS_ROOT", tmp_path)
    rows = [
        _arm_row("f1", "uncompacted", 0.9),
        _arm_row("f1", "current", 0.4),
        _arm_row("f1", RECOVERY, 0.68),
    ]
    summary = build_summary("t", rows, runs_per_arm=1)
    assert summary["recovery_gain"] == pytest.approx(0.28)
    assert summary["arms"][RECOVERY]["recall_pct"] == pytest.approx(0.68)

    _, md_path = write_summary("t", summary)
    md = md_path.read_text(encoding="utf-8")
    assert "检索兜底收益" in md
    assert "+28.0%" in md
