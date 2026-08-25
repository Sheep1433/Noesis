import pytest

from evals.memory_cortex.retrieval_eval import evaluate


@pytest.mark.asyncio
async def test_production_bulletin_ranking_passes_frozen_retrieval_gate() -> None:
    report = await evaluate()
    assert report["component_passed"] is True
    assert report["gate_passed"] is False
    assert report["metrics"]["item_recall_at_5"] >= 0.8
    assert report["metrics"]["item_precision_at_5"] >= 0.7
    assert report["metrics"]["max_bulletin_tokens"] <= 500
