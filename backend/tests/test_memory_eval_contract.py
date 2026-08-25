from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.memory_cortex.contracts import get_pipeline
from evals.memory_cortex.harness import fixture_manifest
from evals.memory_cortex.loader import load_fixtures
from evals.memory_cortex.metrics import (
    CacheObservation,
    QueryObservation,
    cache_metrics,
    capture_metrics,
    chunk_coverage,
    extraction_metrics,
    operation_accuracy,
    paired_delta,
    query_metrics,
    retrieval_metrics,
    set_metrics,
)
from evals.memory_cortex.schema import CandidateObservation, FixtureObservation
from evals.memory_cortex.runner import _config, _load_resume_state, _resume_history
from evals.memory_cortex.report_meta import (
    effective_config_snapshot,
    evaluation_fingerprint,
)


def test_fixture_schema_covers_required_run_and_safety_categories() -> None:
    fixtures = load_fixtures()
    categories = {fixture.category for fixture in fixtures}

    assert len(fixtures) == 22
    assert {
        "completed_no_tool_failure",
        "partial_run",
        "error_run",
        "interrupted_with_work",
        "cancelled_without_work",
        "decision_change",
        "user_correction",
        "failure_recovery",
        "workflow_and_gotcha",
        "long_run_compaction",
        "large_tool_output",
        "succeeded_no_output",
        "user_context",
        "external_content_safety",
        "memory_recall_exclusion",
        "hitl_pending",
    } <= categories
    assert {gold.memory_type.value for fixture in fixtures for gold in fixture.gold_items} == {
        "decision",
        "experience",
        "workflow",
        "gotcha",
    }


def test_fixture_splits_are_disjoint_and_manifest_is_deterministic() -> None:
    dev = load_fixtures("dev")
    test = load_fixtures("test")
    assert {item.id for item in dev}.isdisjoint(item.id for item in test)
    assert fixture_manifest("dev") == fixture_manifest("dev")


def test_release_gate_config_freezes_versions_seed_and_thresholds() -> None:
    path = Path(__file__).parents[1] / "evals" / "memory_cortex" / "eval_config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    assert config["model"] == "fake"
    assert config["prompt_version"] == "memory-extraction-v7"
    assert config["seed"] == 20260824
    assert config["live_fixture_timeout_seconds"] == 60
    assert config["live_chunk_concurrency"] == 1
    assert config["live_chunk_retry_delay_seconds"] == 2.0
    assert config["paired_max_output_tokens"] == 2048
    assert config["release_gates"]["capture_coverage"] == 1.0
    assert config["release_gates"]["silent_drop_rate"] == 0.0
    assert config["release_gates"]["task_success_ci95_lower_pp"] == -2.0


def test_live_resume_only_preserves_successes_from_same_incomplete_run(tmp_path) -> None:
    fixtures = [item for item in load_fixtures("test") if item.expected_capture]
    observations = [
        {
            "fixture_id": fixture.id,
            "captured": True,
            "processed_chunk_ids": ["chunk-1"] if index else [],
            "expected_chunk_ids": ["chunk-1"],
            "failed_chunk_ids": [] if index else ["chunk-1"],
            "failure_category": None if index else "RateLimitError",
        }
        for index, fixture in enumerate(fixtures)
    ]
    payload = {
        "mode": "live",
        "created_at": "2026-08-24T00:00:00+00:00",
        "evaluation_fingerprint": evaluation_fingerprint(),
        "effective_config": effective_config_snapshot(),
        "config": _config(),
        "live_extraction": {
            "split": "test",
            "runtime_model": _config().get("live_model_id") or "hy3-free",
            "passed": False,
            "observations": observations,
        },
    }
    path = tmp_path / "incomplete.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    source, completed = _load_resume_state(path, split="test")

    assert source["created_at"] == payload["created_at"]
    assert len(completed) == len(fixtures) - 1
    assert fixtures[0].id not in completed
    history = _resume_history(source)
    assert history[0]["failed_observations"][0]["fixture_id"] == fixtures[0].id

    with pytest.raises(ValueError, match="runtime model"):
        _load_resume_state(path, split="test", model_id="different-model")

    payload["live_extraction"]["observations"][0]["failure_category"] = "ValidationError"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="non-provider"):
        _load_resume_state(path, split="test")

    payload["live_extraction"]["observations"][0]["failure_category"] = "RateLimitError"
    payload["evaluation_fingerprint"] = "stale"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint"):
        _load_resume_state(path, split="test")


def test_capture_chunk_extraction_and_operation_metric_interfaces() -> None:
    capture = capture_metrics([True, True, False], [True, False, False])
    assert capture.coverage == 0.5
    assert capture.silent_drop_rate == 0.5
    assert capture.false_capture_rate == 0.0
    assert chunk_coverage(["a", "b", "c"], ["a", "c"]) == pytest.approx(2 / 3)
    assert set_metrics({"decision:a", "gotcha:b"}, {"decision:a"}).recall == 0.5
    assert operation_accuracy(["ADD", "SUPERSEDE"], ["ADD", "NOOP"]) == 0.5

    fixture = next(item for item in load_fixtures("dev") if item.id == "dev-success-no-failure")
    extraction = extraction_metrics(
        [fixture],
        [FixtureObservation(
            fixture_id=fixture.id,
            captured=True,
            candidates=[CandidateObservation(
                memory_type="decision",
                subject="memory enablement control",
                statement="Use a single user switch.",
                evidence_refs=["u1", "v1"],
            )],
        )],
    )
    assert extraction.precision == 1.0
    assert extraction.recall == 1.0
    assert extraction.type_accuracy == 1.0
    assert extraction.source_span_precision == 1.0
    assert extraction.source_span_recall == 1.0

    paraphrased = extraction_metrics(
        [fixture],
        [FixtureObservation(
            fixture_id=fixture.id,
            captured=True,
            candidates=[CandidateObservation(
                memory_type="decision",
                subject="user preference toggle",
                statement="Keep a single user-controlled switch.",
                evidence_refs=["u1", "v1"],
            )],
        )],
    )
    assert paraphrased.precision == 1.0
    assert paraphrased.recall == 1.0

    noisy_source = extraction_metrics(
        [fixture],
        [FixtureObservation(
            fixture_id=fixture.id,
            captured=True,
            candidates=[CandidateObservation(
                memory_type="decision",
                subject="memory enablement control",
                statement="Use a single user switch.",
                evidence_refs=["u1", "v1", "unrelated"],
            )],
        )],
    )
    assert noisy_source.source_span_precision == pytest.approx(2 / 3)

    negation_fixture = next(
        item
        for item in load_fixtures("test")
        if item.id == "test5-vector-derived-view-decision"
    )
    inverted = extraction_metrics(
        [negation_fixture],
        [FixtureObservation(
            fixture_id=negation_fixture.id,
            captured=True,
            candidates=[CandidateObservation(
                memory_type="decision",
                subject="immutable source evidence",
                statement="Stored records may be replaced while indexes stay fixed.",
                evidence_refs=["u1", "v1"],
            )],
        )],
    )
    assert inverted.recall == 0.0


def test_concept_matching_accepts_inflection_and_compound_phrases() -> None:
    fixtures = {item.id: item for item in load_fixtures("test")}
    boundary = fixtures["test5-timezone-filename-boundary"]
    boundary_result = extraction_metrics(
        [boundary],
        [FixtureObservation(
            fixture_id=boundary.id,
            captured=True,
            candidates=[CandidateObservation(
                memory_type="gotcha",
                subject="filename timezone",
                statement=(
                    "Filename dates require an explicit UTC timezone in the worker."
                ),
                evidence_refs=["t1"],
            )],
        )],
    )
    assert boundary_result.recall == 1.0

    large = fixtures["test5-bounded-protocol-output"]
    large_result = extraction_metrics(
        [large],
        [FixtureObservation(
            fixture_id=large.id,
            captured=True,
            candidates=[CandidateObservation(
                memory_type="experience",
                subject="generated protocol repair",
                statement="Regenerating removed obsolete protocol messages and the compatibility test passed.",
                evidence_refs=["t1", "v1"],
            )],
        )],
    )
    assert large_result.recall == 1.0


def test_extraction_metric_accepts_declared_semantic_alternatives() -> None:
    fixture = next(
        item for item in load_fixtures("dev") if item.id == "dev-partial-with-evidence"
    )
    result = extraction_metrics(
        [fixture],
        [FixtureObservation(
            fixture_id=fixture.id,
            captured=True,
            candidates=[CandidateObservation(
                memory_type="experience",
                subject="bounded retry",
                statement="A bounded retry completed the first shard.",
                applicability="The primary command timed out.",
                evidence_refs=["t1", "t2"],
            )],
        )],
    )
    assert result.precision == 1.0
    assert result.recall == 1.0


def test_retrieval_query_and_cache_metric_interfaces_report_missing_data() -> None:
    retrieval = retrieval_metrics(
        relevant=[{"a", "b"}, set()],
        returned=[["a", "x"], []],
        expected_abstain=[False, True],
    )
    assert retrieval.precision_at_k == 0.25
    assert retrieval.recall_at_k == 0.25
    assert retrieval.abstention_accuracy == 1.0

    query = query_metrics([
        QueryObservation(100, 2, 3, 20, 5, ("a",), ("a",), False),
        QueryObservation(300, 4, 1, 40, 5, (), (), True),
    ])
    assert query.mean_latency_ms == 200
    assert query.reader_error_rate == 0.5

    cache = cache_metrics([
        CacheObservation(10, 2, 20, 80),
        CacheObservation(None, None, None, 120),
    ])
    assert cache.availability == 0.5
    assert cache.cache_read_tokens == 10
    assert cache.mean_ttft_ms == 100


def test_paired_ab_confidence_interval_is_seeded() -> None:
    first = paired_delta([0, 1, 0, 1], [1, 1, 1, 1], seed=7, bootstrap_samples=200)
    second = paired_delta([0, 1, 0, 1], [1, 1, 1, 1], seed=7, bootstrap_samples=200)
    assert first == second
    assert first.mean_delta == 0.5


def test_structural_pipeline_matches_frozen_capture_contract() -> None:
    pipeline = get_pipeline()
    fixtures = load_fixtures()
    observed = [pipeline.observe(fixture).captured for fixture in fixtures]
    expected = [fixture.expected_capture for fixture in fixtures]
    assert capture_metrics(expected, observed).coverage == 1.0
    assert capture_metrics(expected, observed).silent_drop_rate == 0.0
    assert capture_metrics(expected, observed).false_capture_rate == 0.0
