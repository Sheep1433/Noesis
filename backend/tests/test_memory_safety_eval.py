import pytest

from evals.memory_cortex.safety_eval import evaluate


@pytest.mark.asyncio
async def test_zero_tolerance_safety_gate_with_malicious_index_candidates() -> None:
    report = await evaluate()
    assert report["component_passed"] is True
    assert report["gate_passed"] is False
    assert report["authoritative_returned_ids"] == ["memory-valid"]
    assert all(value == 0 for value in report["counters"].values())
