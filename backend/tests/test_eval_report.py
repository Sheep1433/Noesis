"""aggregate 汇总与质量门。"""

from evals.report import build_aggregate, compute_item_passed


def test_aggregate_coverage_and_dataset_warning():
    scores = [
        {
            "passed": True,
            "l0": {"passed": True},
            "l1": {"passed": True},
            "coverage": {
                "skipped": False,
                "point_coverage_recall": 1.0,
                "point_precision": 0.5,
            },
            "rag": {"skipped": True},
        },
        {
            "passed": True,
            "l0": {"passed": True},
            "l1": {"passed": True},
            "coverage": {
                "skipped": False,
                "point_coverage_recall": 0.5,
                "point_precision": None,
            },
            "rag": {"skipped": True},
        },
    ]
    agg = build_aggregate(scores)
    assert agg["coverage"]["point_coverage_recall_mean"] == 0.75
    assert agg["dataset_size_warning"] is True


def test_compute_item_passed_uses_coverage_when_golden():
    gt = {"golden_test_points": [{"scene_name": "S", "point_name": "P"}]}
    scores = {
        "l0": {"passed": True},
        "l1": {"passed": False},
        "coverage": {"skipped": False, "point_coverage_recall": 1.0},
        "l2": {"skipped": True},
    }
    assert compute_item_passed(scores, gt) is True
