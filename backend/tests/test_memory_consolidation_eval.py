from evals.memory_cortex.consolidation_eval import evaluate


def test_all_six_consolidation_operations_are_frozen_and_pass() -> None:
    report = evaluate()
    assert {case["expected"] for case in report["cases"]} == {
        "ADD", "REINFORCE", "UPDATE", "SUPERSEDE", "CONTRADICT", "NOOP"
    }
    assert report["operation_accuracy"] == 1.0
    assert report["gate_passed"] is True
