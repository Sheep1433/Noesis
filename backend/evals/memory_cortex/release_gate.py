"""Fail-closed aggregator for all machine-memory release evidence."""

from __future__ import annotations

import argparse
import json
import uuid
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path


from evals.memory_cortex.loader import load_fixtures
from evals.memory_cortex.report_meta import (
    effective_config_snapshot,
    evaluation_fingerprint,
)
from noesis.config.env import MachineMemoryConfig, ModelConfig


REQUIRED_REPORTS = {
    "live_dev_extraction": ("live-dev.json", "memory-eval-report-v1", "live"),
    "live_test_extraction": ("live-test.json", "memory-eval-report-v1", "live"),
    "consolidation": (
        "consolidation.json",
        "memory-consolidation-eval-v1",
        "production_deterministic",
    ),
    "retrieval_bulletin": (
        "retrieval-integration.json",
        "memory-retrieval-integration-v1",
        "production_integration",
    ),
    "safety": (
        "safety-integration.json",
        "memory-safety-integration-v1",
        "production_integration",
    ),
    "paired_ab": (
        "paired-integration.json",
        "memory-paired-integration-v1",
        "production_integration",
    ),
    "cache": (
        "cache-integration.json",
        "memory-cache-integration-v1",
        "production_integration",
    ),
}
EVAL_CONFIG = json.loads(
    Path(__file__).with_name("eval_config.json").read_text(encoding="utf-8")
)


def _is_uuid(value: object) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def _passed(name: str, report: dict) -> bool:
    gates = EVAL_CONFIG["release_gates"]
    metrics = report.get("metrics") or {}
    if name in {"live_dev_extraction", "live_test_extraction"}:
        capture = (report.get("structural") or {}).get("capture") or {}
        live = report.get("live_extraction") or {}
        metrics = live.get("metrics") or {}
        split = live.get("split")
        expected_fixtures = (
            sum(fixture.expected_capture for fixture in load_fixtures(split))
            if split in {"dev", "test"}
            else -1
        )
        observations = live.get("observations") or []
        return bool(
            live.get("passed")
            and capture.get("coverage") == gates["capture_coverage"]
            and capture.get("silent_drop_rate") == gates["silent_drop_rate"]
            and metrics.get("precision", 0) >= gates["extraction_precision"]
            and metrics.get("recall", 0) >= gates["extraction_recall"]
            and metrics.get("source_span_precision", 0)
            >= gates["source_span_precision"]
            and metrics.get("source_span_recall", 0) >= gates["source_span_recall"]
            and live.get("fixtures") == expected_fixtures
            and len(observations) == expected_fixtures
            and not any(item.get("failed_chunk_ids") for item in observations)
        )
    if name == "consolidation":
        cases = report.get("cases") or []
        operations = {item.get("expected") for item in cases}
        return bool(
            report.get("gate_passed")
            and report.get("operation_accuracy", 0) >= gates["operation_accuracy"]
            and operations
            == {"ADD", "REINFORCE", "UPDATE", "SUPERSEDE", "CONTRADICT", "NOOP"}
            and all(item.get("expected") == item.get("observed") for item in cases)
        )
    if name == "retrieval_bulletin":
        return bool(
            report.get("gate_passed")
            and report.get("query_count", 0) >= EVAL_CONFIG["min_retrieval_queries"]
            and metrics.get("exact_evidence_recall_at_5", 0)
            >= gates["exact_evidence_recall_at_5"]
            and metrics.get("precision_at_5", 0) >= gates["precision_at_5"]
            and metrics.get("bulletin_recall", 0) >= gates["bulletin_recall"]
            and metrics.get("bulletin_precision", 0) >= gates["bulletin_precision"]
            and metrics.get("fast_p95_ms", float("inf")) <= gates["fast_p95_ms"]
            and metrics.get("max_bulletin_tokens", float("inf"))
            <= EVAL_CONFIG["bulletin_max_tokens"]
        )
    if name == "safety":
        counters = report.get("counters") or {}
        required = {
            "cross_user_leaks",
            "cross_project_leaks",
            "explicit_cross_user_leaks",
            "stale_or_disabled_injections",
            "low_trust_command_injections",
            "recall_loop_items",
            "disabled_residual_injections",
            "deleted_residual_injections",
            "workspace_residuals",
        }
        required_cases = {
            "cross_user",
            "cross_project",
            "explicit_cross_user",
            "stale_disabled",
            "low_trust_command",
            "recall_loop",
            "user_disabled",
            "deleted_pg",
            "workspace_deleted",
        }
        cases = report.get("cases") or {}
        return bool(
            report.get("gate_passed")
            and required <= counters.keys()
            and all(counters[key] == 0 for key in required)
            and required_cases <= cases.keys()
            and all(cases[key].get("executed") is True for key in required_cases)
            and cases["recall_loop"].get("automatic_private_context") is True
            and cases["recall_loop"].get("consolidation_executed") is True
            and cases["recall_loop"].get("evidence_count_checked") is True
        )
    if name == "paired_ab":
        runtime_fixture = json.loads(
            (
                Path(__file__).with_name("fixtures") / "runtime_integration.json"
            ).read_text(encoding="utf-8")
        )
        expected_tasks = {
            task["id"]: set(task["success_criteria"])
            for task in runtime_fixture["tasks"]
        }
        success = metrics.get("task_success_delta") or {}
        failures = metrics.get("repeated_failure_reduction") or {}
        required_costs = (
            "memory_off_tokens",
            "memory_on_tokens",
            "memory_off_cache_read_tokens",
            "memory_on_cache_read_tokens",
            "memory_off_cache_write_tokens",
            "memory_on_cache_write_tokens",
            "memory_off_uncached_input_tokens",
            "memory_on_uncached_input_tokens",
            "memory_off_cache_read_ratio",
            "memory_on_cache_read_ratio",
            "memory_on_bulletin_degraded_rate",
            "memory_on_expected_recall_rate",
            "memory_off_mean_ttft_ms",
            "memory_on_mean_ttft_ms",
            "memory_query_latency_ms",
            "monetary_cost",
            "memory_off_tool_calls",
            "memory_on_tool_calls",
        )
        observations = report.get("observations") or []
        task_ids = [item.get("task_id") for item in observations]
        on_user_id = report.get("on_user_id")
        off_user_id = report.get("off_user_id")
        observation_count = len(observations)
        observed_off_rate = (
            sum(
                bool(item.get("memory_off", {}).get("success")) for item in observations
            )
            / observation_count
            if observation_count
            else -1
        )
        observed_on_rate = (
            sum(bool(item.get("memory_on", {}).get("success")) for item in observations)
            / observation_count
            if observation_count
            else -1
        )
        return bool(
            report.get("gate_passed")
            and report.get("task_count", 0) >= EVAL_CONFIG["min_paired_tasks"]
            and report.get("task_count") == len(expected_tasks)
            and len(observations) == report.get("task_count")
            and set(task_ids) == set(expected_tasks)
            and len(task_ids) == len(set(task_ids))
            and all(task_ids)
            and _is_uuid(on_user_id)
            and _is_uuid(off_user_id)
            and on_user_id != off_user_id
            and report.get("on_snapshot_digest") == report.get("off_snapshot_digest")
            and bool(report.get("on_snapshot_digest"))
            and report.get("automatic_capture_paused") is True
            and all(
                item.get("on_user_id") == on_user_id
                and item.get("off_user_id") == off_user_id
                and item.get("paired_seed") is not None
                and item.get("model_seed_applied") is True
                and isinstance(item.get("memory_on"), dict)
                and isinstance(item.get("memory_off"), dict)
                and isinstance(item["memory_on"].get("success"), bool)
                and isinstance(item["memory_off"].get("success"), bool)
                and set(item["memory_on"].get("matched_criteria") or [])
                <= expected_tasks[item["task_id"]]
                and set(item["memory_off"].get("matched_criteria") or [])
                <= expected_tasks[item["task_id"]]
                and item["memory_on"]["success"]
                == (
                    set(item["memory_on"].get("matched_criteria") or [])
                    == expected_tasks[item["task_id"]]
                )
                and item["memory_off"]["success"]
                == (
                    set(item["memory_off"].get("matched_criteria") or [])
                    == expected_tasks[item["task_id"]]
                )
                and isinstance(item.get("expected_memory_id"), str)
                and isinstance(item["memory_on"].get("bulletin_memory_ids"), list)
                and isinstance(item["memory_on"].get("bulletin_degraded"), bool)
                and item["memory_off"].get("bulletin_memory_ids") == []
                and item["memory_off"].get("bulletin_degraded") is False
                and isinstance(item["memory_on"].get("tokens"), (int, float))
                and isinstance(item["memory_off"].get("tokens"), (int, float))
                and isinstance(item["memory_on"].get("ttft_ms"), (int, float))
                and isinstance(item["memory_off"].get("ttft_ms"), (int, float))
                and isinstance(item["memory_on"].get("tool_calls"), int)
                and isinstance(item["memory_off"].get("tool_calls"), int)
                and all(
                    isinstance(item[group].get(field), (int, float))
                    for group in ("memory_on", "memory_off")
                    for field in (
                        "cache_read_tokens",
                        "cache_write_tokens",
                        "uncached_input_tokens",
                        "cache_read_ratio",
                    )
                )
                for item in observations
            )
            and success.get("ci95_low", -1) >= gates["task_success_ci95_lower_pp"] / 100
            and (
                success.get("ci95_low", 0) > 0
                or (
                    failures.get("measurement_status") == "measured"
                    and failures.get("ci95_low", 0) > 0
                )
            )
            and failures.get("measurement_status") in {"measured", "not_measured"}
            and all(
                isinstance(metrics.get(key), (int, float)) for key in required_costs
            )
            and (metrics.get("background_extraction") or {}).get(
                "measurement_status"
            )
            == "paused_by_design"
            and math.isclose(
                metrics.get("memory_off_task_success_rate", -1), observed_off_rate
            )
            and math.isclose(
                metrics.get("memory_on_task_success_rate", -1), observed_on_rate
            )
            and math.isclose(
                success.get("mean_delta", -2), observed_on_rate - observed_off_rate
            )
            and metrics.get("memory_off_tokens")
            == sum(item["memory_off"]["tokens"] for item in observations)
            and metrics.get("memory_on_tokens")
            == sum(item["memory_on"]["tokens"] for item in observations)
            and metrics.get("memory_off_tool_calls")
            == sum(item["memory_off"]["tool_calls"] for item in observations)
            and metrics.get("memory_on_tool_calls")
            == sum(item["memory_on"]["tool_calls"] for item in observations)
            and metrics.get("memory_off_cache_read_tokens")
            == sum(item["memory_off"]["cache_read_tokens"] for item in observations)
            and metrics.get("memory_on_cache_read_tokens")
            == sum(item["memory_on"]["cache_read_tokens"] for item in observations)
            and metrics.get("memory_off_cache_write_tokens")
            == sum(item["memory_off"]["cache_write_tokens"] for item in observations)
            and metrics.get("memory_on_cache_write_tokens")
            == sum(item["memory_on"]["cache_write_tokens"] for item in observations)
            and metrics.get("memory_off_uncached_input_tokens")
            == sum(item["memory_off"]["uncached_input_tokens"] for item in observations)
            and metrics.get("memory_on_uncached_input_tokens")
            == sum(item["memory_on"]["uncached_input_tokens"] for item in observations)
            and math.isclose(
                metrics.get("memory_off_cache_read_ratio", -1),
                sum(item["memory_off"]["cache_read_ratio"] for item in observations)
                / observation_count,
            )
            and math.isclose(
                metrics.get("memory_on_cache_read_ratio", -1),
                sum(item["memory_on"]["cache_read_ratio"] for item in observations)
                / observation_count,
            )
            and math.isclose(
                metrics.get("memory_on_bulletin_degraded_rate", -1),
                sum(
                    float(item["memory_on"]["bulletin_degraded"])
                    for item in observations
                )
                / observation_count,
            )
            and math.isclose(
                metrics.get("memory_on_expected_recall_rate", -1),
                sum(
                    float(
                        item["expected_memory_id"]
                        in item["memory_on"]["bulletin_memory_ids"]
                    )
                    for item in observations
                )
                / observation_count,
            )
        )
    if name == "cache":
        scenarios = report.get("scenarios") or {}
        required_scenarios = {
            "same_run",
            "new_run_same_bulletin",
            "new_run_changed_bulletin",
            "deep_query_tool_result",
        }
        calls = [
            call
            for scenario in scenarios.values()
            for call in scenario.get("calls", [])
        ]
        return bool(
            report.get("gate_passed")
            and required_scenarios <= scenarios.keys()
            and metrics.get("cache_read_availability") == 1.0
            and metrics.get("ttft_availability") == 1.0
            and metrics.get("cache_read_tokens", 0) > 0
            and isinstance(metrics.get("uncached_input_tokens"), (int, float))
            and report.get("call_count", 0) >= 8
            and len(calls) == report.get("call_count")
            and report.get("failure_count") == 0
            and all(call.get("failure_category") is None for call in calls)
            and all(
                (scenarios[name].get("calls") or [{}, {}])[-1].get(
                    "cache_read_tokens", 0
                )
                > 0
                for name in (
                    "same_run",
                    "new_run_same_bulletin",
                    "deep_query_tool_result",
                )
            )
            and scenarios["same_run"].get("bulletin_hash_equal") is True
            and scenarios["same_run"].get("bulletin_text_equal") is True
            and scenarios["same_run"].get("middleware_second_freeze_was_noop") is True
            and scenarios["new_run_same_bulletin"].get("bulletin_hash_equal") is True
            and scenarios["new_run_same_bulletin"].get("bulletin_text_equal") is True
            and scenarios["new_run_changed_bulletin"].get("bulletin_hash_changed")
            is True
            and scenarios["new_run_changed_bulletin"].get("bulletin_text_changed")
            is True
            and scenarios["new_run_changed_bulletin"].get("stable_prefix_hash_equal")
            is True
            and scenarios["new_run_changed_bulletin"].get(
                "bulletin_after_stable_prefix"
            )
            is True
            and scenarios["deep_query_tool_result"].get("frozen_bulletin_unchanged")
            is True
        )
    return False


def _validate_report(
    name: str,
    report: dict,
    *,
    expected_version: str,
    expected_mode: str,
    now: datetime,
) -> str | None:
    if report.get("report_version") != expected_version:
        return "wrong_report_version"
    if report.get("mode") != expected_mode:
        return "wrong_mode"
    if report.get("evaluation_fingerprint") != evaluation_fingerprint():
        return "stale_fingerprint"
    if report.get("effective_config") != effective_config_snapshot():
        return "wrong_effective_config"
    try:
        created_at = datetime.fromisoformat(str(report["created_at"]))
    except (KeyError, TypeError, ValueError):
        return "invalid_created_at"
    if created_at.tzinfo is None:
        return "invalid_created_at"
    if created_at > now + timedelta(minutes=5):
        return "future_report"
    if now - created_at > timedelta(days=7):
        return "stale_report"
    if name in {"live_dev_extraction", "live_test_extraction"}:
        expected_split = "dev" if name == "live_dev_extraction" else "test"
        live = report.get("live_extraction") or {}
        config = report.get("config") or {}
        expected_model = MachineMemoryConfig.extraction_model or ModelConfig.model_name
        if live.get("split") != expected_split:
            return "wrong_split"
        if live.get("runtime_model") != expected_model:
            return "wrong_runtime_model"
        if config.get("fixture_revision") != EVAL_CONFIG["fixture_revision"]:
            return "wrong_fixture_revision"
        if config.get("prompt_version") != EVAL_CONFIG["prompt_version"]:
            return "wrong_prompt_version"
        if name == "live_test_extraction" and report.get("test_split_contaminated"):
            return "test_split_contaminated"
    elif report.get("fixture_revision") != EVAL_CONFIG["fixture_revision"]:
        return "wrong_fixture_revision"
    if name == "paired_ab" and report.get("seed") != EVAL_CONFIG["seed"]:
        return "wrong_seed"
    if (
        name in {"paired_ab", "cache"}
        and report.get("runtime_max_output_tokens")
        != EVAL_CONFIG["paired_max_output_tokens"]
    ):
        return "wrong_runtime_max_output_tokens"
    if report.get("model_observations_reused"):
        return "reused_model_observations"
    return None


def aggregate(report_dir: Path, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    gates: dict[str, dict[str, object]] = {}
    for name, (filename, expected_version, expected_mode) in REQUIRED_REPORTS.items():
        path = report_dir / filename
        if not path.is_file():
            gates[name] = {"passed": False, "reason": "missing_report"}
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            gates[name] = {"passed": False, "reason": "invalid_report"}
            continue
        invalid_reason = _validate_report(
            name,
            report,
            expected_version=expected_version,
            expected_mode=expected_mode,
            now=now,
        )
        gates[name] = {
            "passed": invalid_reason is None and _passed(name, report),
            "report": filename,
            "report_version": report.get("report_version"),
        }
        if invalid_reason:
            gates[name]["reason"] = invalid_reason
    return {
        "report_version": "memory-release-gate-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "gates": gates,
        "release_ready": all(bool(value["passed"]) for value in gates.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--report-dir", type=Path, default=Path(__file__).parent / "reports"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = aggregate(args.report_dir)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["release_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
