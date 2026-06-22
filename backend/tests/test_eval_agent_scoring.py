"""Agent 评测规则 scoring 单元测试（不调 LLM）。"""

import json
from pathlib import Path

from evals.agent.scoring import evaluate_criterion, score_item, score_rule_criteria


def test_file_exists_and_contains(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "out.txt").write_text("hello world", encoding="utf-8")

    r1 = evaluate_criterion(ws, {"id": "c1", "type": "file_exists", "path": "out.txt"})
    assert r1["passed"] is True

    r2 = evaluate_criterion(
        ws,
        {"id": "c2", "type": "file_contains", "path": "out.txt", "substring": "world"},
    )
    assert r2["passed"] is True

    r3 = evaluate_criterion(ws, {"id": "c3", "type": "file_not_exists", "path": "missing.txt"})
    assert r3["passed"] is True


def test_json_field_min(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "results").mkdir(parents=True)
    payload = {"cases": {"text_shoe": {"precision": 0.85}}}
    (ws / "results" / "predictions.json").write_text(json.dumps(payload), encoding="utf-8")

    r = evaluate_criterion(
        ws,
        {
            "id": "c1",
            "type": "json_field_min",
            "path": "results/predictions.json",
            "field": "cases.text_shoe.precision",
            "min": 0.8,
        },
    )
    assert r["passed"] is True

    _, rate = score_rule_criteria(
        ws,
        [
            {
                "id": "c1",
                "type": "json_field_min",
                "path": "results/predictions.json",
                "field": "cases.text_shoe.precision",
                "min": 0.8,
            }
        ],
    )
    assert rate == 1.0


def test_score_item_skip_semantic(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "output").mkdir()
    (ws / "output" / "bio.md").write_text("## 早年\nx", encoding="utf-8")

    run_output = {
        "final_text": "done",
        "completed": True,
        "error": None,
        "tool_stats": {"web_search": 2},
    }
    ground_truth = {
        "criteria": [
            {"id": "c1", "type": "file_exists", "path": "output/bio.md"},
        ],
        "semantic_rubric": ["应包含人物信息"],
        "expected_tools": ["web_search"],
    }
    result = score_item(run_output, ground_truth, ws, skip_semantic=True)
    assert result["rule_score"] == 1.0
    assert result["semantic"]["skipped"] is True
    assert result["overall_score"] == 1.0
    assert result["tool_coverage"]["rate"] == 1.0
