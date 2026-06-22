"""压缩评测报告汇总测试。"""

from evals.compression.report import build_summary, summarize_fixture_runs


def _sample_run(fixture_id: str, score: float) -> dict:
    return {
        "fixture_id": fixture_id,
        "compression": {"compression_ratio": 0.4, "pre_tokens": 1000, "post_tokens": 600},
        "probes": [
            {
                "probe_id": "p1",
                "type": "recall",
                "scores": {
                    "accuracy": score,
                    "artifact_trail": score,
                    "context_awareness": score,
                    "continuity": score,
                    "completeness": score,
                },
                "overall_probe_score": score,
            }
        ],
    }


def test_summarize_fixture_runs_median():
    runs = [_sample_run("debug_session", 4.0), _sample_run("debug_session", 2.0)]
    summary = summarize_fixture_runs(runs)
    assert summary["fixture_id"] == "debug_session"
    assert summary["fixture_score"] == 3.0
    assert summary["runs"] == 2


def test_build_summary_with_delta(tmp_path):
    import json

    baseline_dir = tmp_path / "baseline"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "summary.json").write_text(
        json.dumps(
            {
                "fixtures": [
                    {
                        "fixture_id": "debug_session",
                        "fixture_score": 3.0,
                        "dimension_medians": {
                            "accuracy": 3.0,
                            "artifact_trail": 3.0,
                            "context_awareness": 3.0,
                            "continuity": 3.0,
                            "completeness": 3.0,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    current = [
        {
            "fixture_id": "debug_session",
            "fixture_score": 4.0,
            "dimension_medians": {
                "accuracy": 4.0,
                "artifact_trail": 4.0,
                "context_awareness": 4.0,
                "continuity": 4.0,
                "completeness": 4.0,
            },
        }
    ]
    summary = build_summary("tweak", current, compare_to=baseline_dir)
    row = summary["fixtures"][0]
    assert row["delta_fixture_score"] == 1.0
