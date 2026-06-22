"""从 dataset.jsonl 生成 promptfoo 测试用例。"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[3]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import os
from typing import Any, Dict, List, Optional

from evals.case.dataset import DEFAULT_DATASET, load_dataset
from evals.case.scoring import eval_scope

_SCORING = Path(__file__).resolve().parent.parent / "scoring.py"


def _asserts_for_scope(scope: str) -> List[Dict[str, Any]]:
    asserts: List[Dict[str, Any]] = [
        {
            "type": "python",
            "value": f"file://{_SCORING}:assert_l0",
            "metric": "l0",
        },
    ]
    if scope in ("testpoints", "full"):
        asserts.append(
            {
                "type": "python",
                "value": f"file://{_SCORING}:assert_coverage",
                "metric": "point_coverage_recall",
            }
        )
    if scope in ("cases", "full"):
        asserts.append(
            {
                "type": "python",
                "value": f"file://{_SCORING}:assert_rag",
                "metric": "rag_hit_at_3",
            }
        )
    return asserts


def generate_tests(config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    config = config or {}
    dataset_raw = config.get("dataset") or os.environ.get("NOESIS_CASE_EVAL_DATASET") or os.environ.get("NOESIS_EVAL_DATASET")
    dataset_path = Path(dataset_raw) if dataset_raw else DEFAULT_DATASET
    scope = config.get("scope") or eval_scope()
    item_id = config.get("item_id") or os.environ.get("NOESIS_CASE_EVAL_ITEM_ID") or os.environ.get("NOESIS_EVAL_ITEM_ID")
    limit = config.get("limit")
    if limit is None:
        raw_limit = os.environ.get("NOESIS_CASE_EVAL_LIMIT") or os.environ.get("NOESIS_EVAL_LIMIT")
        if raw_limit:
            limit = int(raw_limit)

    items = load_dataset(dataset_path)
    if item_id:
        items = [i for i in items if i["id"] == item_id]
        if not items:
            raise ValueError(f"未找到 item: {item_id}")
    if limit is not None:
        items = items[: int(limit)]

    asserts = _asserts_for_scope(scope)
    return [
        {
            "description": item["id"],
            "vars": {
                "item": item,
                "item_id": item["id"],
                "scenario_description": item.get("scenario_description") or item.get("query") or "",
                "scope": scope,
            },
            "assert": asserts,
        }
        for item in items
    ]
