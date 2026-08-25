import json
from pathlib import Path


def test_integration_fixture_is_frozen_with_twelve_retrieval_queries() -> None:
    path = Path(__file__).parents[1] / "evals/memory_cortex/fixtures/integration.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert fixture["schema_version"] == "memory-integration-fixture-v1"
    assert fixture["revision"] == "2026-08-24.10"
    assert len(fixture["retrieval"]) == 12
    assert len({case["id"] for case in fixture["retrieval"]}) == 12


def test_runtime_integration_fixture_has_twelve_unique_opaque_tasks() -> None:
    path = (
        Path(__file__).parents[1]
        / "evals/memory_cortex/fixtures/runtime_integration.json"
    )
    fixture = json.loads(path.read_text(encoding="utf-8"))
    tasks = fixture["tasks"]

    assert fixture["schema_version"] == "memory-runtime-integration-v2"
    assert fixture["revision"] == "2026-08-24.10"
    assert len(tasks) == 12
    assert len({task["id"] for task in tasks}) == 12
    assert all(task["success_criteria"] for task in tasks)
