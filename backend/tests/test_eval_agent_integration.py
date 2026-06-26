"""Agent 评测集成测试（默认 skip，需真实 LLM）。"""

import os

import pytest

from evals.agent.perf.dataset import DEFAULT_DATASET, filter_items, load_dataset
from evals.agent.perf.runner import run_agent_item
from evals.agent.perf.scoring import score_item

pytestmark = pytest.mark.skipif(
    os.environ.get("NOESIS_AGENT_EVAL_INTEGRATION") != "1",
    reason="integration: set NOESIS_AGENT_EVAL_INTEGRATION=1",
)


@pytest.mark.integration
def test_agent_eval_single_item_integration():
    items = filter_items(load_dataset(DEFAULT_DATASET), item_id="dr_code_small_api_fix")
    item = items[0]
    run_output = run_agent_item(
        item,
        dataset_dir=DEFAULT_DATASET.parent,
        eval_run_id="integration-test",
    )
    scoring = score_item(
        run_output,
        dict(item.get("ground_truth") or {}),
        __import__("pathlib").Path(run_output["workspace_path"]),
        skip_semantic=True,
    )
    assert "overall_score" in scoring
    assert run_output.get("latency_ms", 0) >= 0
