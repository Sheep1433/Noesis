from __future__ import annotations

import json
from datetime import datetime, timezone
from datetime import timedelta
from pathlib import Path

from evals.memory_cortex.release_gate import EVAL_CONFIG, REQUIRED_REPORTS, aggregate
from evals.memory_cortex.loader import load_fixtures
from evals.memory_cortex.report_meta import (
    effective_config_snapshot,
    evaluation_fingerprint,
)
from noesis.config.env import MachineMemoryConfig, ModelConfig


def test_release_gate_fails_closed_for_missing_or_failed_reports(tmp_path) -> None:
    report = aggregate(tmp_path)
    assert report["release_ready"] is False
    assert all(not value["passed"] for value in report["gates"].values())

    (tmp_path / "live-dev.json").write_text(
        json.dumps({"live_extraction": {"passed": False}}), encoding="utf-8"
    )
    assert aggregate(tmp_path)["gates"]["live_dev_extraction"]["passed"] is False


def test_release_gate_requires_every_frozen_layer(tmp_path) -> None:
    now = datetime.now(timezone.utc)
    for name, (filename, version, mode) in REQUIRED_REPORTS.items():
        payload = {
            "report_version": version,
            "mode": mode,
            "created_at": now.isoformat(),
            "evaluation_fingerprint": evaluation_fingerprint(),
            "effective_config": effective_config_snapshot(),
            "gate_passed": True,
            "fixture_revision": EVAL_CONFIG["fixture_revision"],
        }
        if name.startswith("live_"):
            split = "dev" if name == "live_dev_extraction" else "test"
            fixture_count = sum(
                fixture.expected_capture for fixture in load_fixtures(split)
            )
            payload["config"] = {
                "fixture_revision": EVAL_CONFIG["fixture_revision"],
                "prompt_version": EVAL_CONFIG["prompt_version"],
            }
            payload["live_extraction"] = {
                "passed": True,
                "split": split,
                "fixtures": fixture_count,
                "observations": [
                    {"fixture_id": f"fixture-{index}", "failed_chunk_ids": []}
                    for index in range(fixture_count)
                ],
                "runtime_model": (
                    MachineMemoryConfig.extraction_model or ModelConfig.model_name
                ),
                "metrics": {
                    "precision": 1.0,
                    "recall": 1.0,
                    "source_span_precision": 1.0,
                    "source_span_recall": 1.0,
                },
            }
            payload["structural"] = {
                "capture": {"coverage": 1.0, "silent_drop_rate": 0.0}
            }
        elif name == "consolidation":
            payload["operation_accuracy"] = 1.0
            payload["cases"] = [
                {"expected": operation, "observed": operation}
                for operation in (
                    "ADD",
                    "REINFORCE",
                    "UPDATE",
                    "SUPERSEDE",
                    "CONTRADICT",
                    "NOOP",
                )
            ]
        elif name == "retrieval_bulletin":
            payload["query_count"] = EVAL_CONFIG["min_retrieval_queries"]
            payload["metrics"] = {
                "exact_evidence_recall_at_5": 1.0,
                "precision_at_5": 1.0,
                "bulletin_recall": 1.0,
                "bulletin_precision": 1.0,
                "fast_p95_ms": 10,
                "max_bulletin_tokens": 100,
            }
        elif name == "safety":
            payload["counters"] = {
                "cross_user_leaks": 0,
                "cross_project_leaks": 0,
                "explicit_cross_user_leaks": 0,
                "stale_or_disabled_injections": 0,
                "low_trust_command_injections": 0,
                "recall_loop_items": 0,
                "disabled_residual_injections": 0,
                "deleted_residual_injections": 0,
                "workspace_residuals": 0,
            }
            payload["cases"] = {
                key: {"executed": True}
                for key in (
                    "cross_user",
                    "cross_project",
                    "explicit_cross_user",
                    "stale_disabled",
                    "low_trust_command",
                    "recall_loop",
                    "user_disabled",
                    "deleted_pg",
                    "workspace_deleted",
                )
            }
            payload["cases"]["recall_loop"].update({
                "automatic_private_context": True,
                "consolidation_executed": True,
                "evidence_count_checked": True,
            })
        elif name == "paired_ab":
            runtime_fixture = json.loads(
                (
                    Path(__file__).parents[1]
                    / "evals/memory_cortex/fixtures/runtime_integration.json"
                ).read_text(encoding="utf-8")
            )
            payload["seed"] = EVAL_CONFIG["seed"]
            payload["runtime_max_output_tokens"] = EVAL_CONFIG[
                "paired_max_output_tokens"
            ]
            payload["task_count"] = EVAL_CONFIG["min_paired_tasks"]
            payload["on_user_id"] = "00000000-0000-0000-0000-000000000011"
            payload["off_user_id"] = "00000000-0000-0000-0000-000000000012"
            payload["on_snapshot_digest"] = "a" * 64
            payload["off_snapshot_digest"] = "a" * 64
            payload["automatic_capture_paused"] = True
            payload["observations"] = [
                {
                    "task_id": task["id"],
                    "expected_memory_id": f"memory-{index}",
                    "on_user_id": payload["on_user_id"],
                    "off_user_id": payload["off_user_id"],
                    "paired_seed": EVAL_CONFIG["seed"] + index,
                    "model_seed_applied": True,
                    "memory_on": {
                        "success": True,
                        "matched_criteria": task["success_criteria"],
                        "tokens": 20,
                        "ttft_ms": 100,
                        "tool_calls": 1,
                        "cache_read_tokens": 5,
                        "cache_write_tokens": 0,
                        "uncached_input_tokens": 15,
                        "cache_read_ratio": 0.25,
                        "bulletin_memory_ids": [f"memory-{index}"],
                        "bulletin_degraded": False,
                    },
                    "memory_off": {
                        "success": False,
                        "matched_criteria": [],
                        "tokens": 10,
                        "ttft_ms": 90,
                        "tool_calls": 1,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 0,
                        "uncached_input_tokens": 10,
                        "cache_read_ratio": 0.0,
                        "bulletin_memory_ids": [],
                        "bulletin_degraded": False,
                    },
                }
                for index, task in enumerate(runtime_fixture["tasks"])
            ]
            payload["metrics"] = {
                "task_success_delta": {"mean_delta": 1.0, "ci95_low": 0.1},
                "repeated_failure_reduction": {"measurement_status": "not_measured"},
                "memory_off_task_success_rate": 0.0,
                "memory_on_task_success_rate": 1.0,
                "memory_off_tokens": EVAL_CONFIG["min_paired_tasks"] * 10,
                "memory_on_tokens": EVAL_CONFIG["min_paired_tasks"] * 20,
                "memory_off_cache_read_tokens": 0,
                "memory_on_cache_read_tokens": EVAL_CONFIG["min_paired_tasks"] * 5,
                "memory_off_cache_write_tokens": 0,
                "memory_on_cache_write_tokens": 0,
                "memory_off_uncached_input_tokens": EVAL_CONFIG["min_paired_tasks"]
                * 10,
                "memory_on_uncached_input_tokens": EVAL_CONFIG["min_paired_tasks"] * 15,
                "memory_off_cache_read_ratio": 0.0,
                "memory_on_cache_read_ratio": 0.25,
                "memory_on_bulletin_degraded_rate": 0.0,
                "memory_on_expected_recall_rate": 1.0,
                "memory_off_mean_ttft_ms": 100,
                "memory_on_mean_ttft_ms": 110,
                "memory_query_latency_ms": 10,
                "background_extraction": {
                    "measurement_status": "paused_by_design",
                    "input_tokens": 0,
                    "output_tokens": 0,
                },
                "monetary_cost": 0,
                "memory_off_tool_calls": EVAL_CONFIG["min_paired_tasks"],
                "memory_on_tool_calls": EVAL_CONFIG["min_paired_tasks"],
            }
        elif name == "cache":
            payload["runtime_max_output_tokens"] = EVAL_CONFIG[
                "paired_max_output_tokens"
            ]
            calls = [
                {"cache_read_tokens": 0, "failure_category": None},
                {"cache_read_tokens": 100, "failure_category": None},
            ]
            payload["scenarios"] = {
                "same_run": {
                    "bulletin_hash_equal": True,
                    "bulletin_text_equal": True,
                    "middleware_second_freeze_was_noop": True,
                    "calls": calls,
                },
                "new_run_same_bulletin": {
                    "bulletin_hash_equal": True,
                    "bulletin_text_equal": True,
                    "calls": calls,
                },
                "new_run_changed_bulletin": {
                    "bulletin_hash_changed": True,
                    "bulletin_text_changed": True,
                    "stable_prefix_hash_equal": True,
                    "bulletin_after_stable_prefix": True,
                    "calls": [
                        {"cache_read_tokens": 100, "failure_category": None},
                        {"cache_read_tokens": 0, "failure_category": None},
                    ],
                },
                "deep_query_tool_result": {
                    "frozen_bulletin_unchanged": True,
                    "calls": calls,
                },
            }
            payload["call_count"] = 8
            payload["failure_count"] = 0
            payload["metrics"] = {
                "cache_read_availability": 1.0,
                "ttft_availability": 1.0,
                "cache_read_tokens": 100,
                "uncached_input_tokens": 20,
            }
        (tmp_path / filename).write_text(json.dumps(payload), encoding="utf-8")

    assert aggregate(tmp_path, now=now)["release_ready"] is True

    cache_path = tmp_path / "cache-integration.json"
    reused = json.loads(cache_path.read_text(encoding="utf-8"))
    reused["model_observations_reused"] = True
    cache_path.write_text(json.dumps(reused), encoding="utf-8")
    report = aggregate(tmp_path, now=now)
    assert report["release_ready"] is False
    assert report["gates"]["cache"]["reason"] == "reused_model_observations"
    reused.pop("model_observations_reused")
    cache_path.write_text(json.dumps(reused), encoding="utf-8")

    test_path = tmp_path / "live-test.json"
    contaminated = json.loads(test_path.read_text(encoding="utf-8"))
    contaminated["test_split_contaminated"] = True
    test_path.write_text(json.dumps(contaminated), encoding="utf-8")
    report = aggregate(tmp_path, now=now)
    assert report["release_ready"] is False
    assert (
        report["gates"]["live_test_extraction"]["reason"] == "test_split_contaminated"
    )

    contaminated.pop("test_split_contaminated")
    contaminated["created_at"] = (now + timedelta(hours=1)).isoformat()
    test_path.write_text(json.dumps(contaminated), encoding="utf-8")
    report = aggregate(tmp_path, now=now)
    assert report["gates"]["live_test_extraction"]["reason"] == "future_report"
