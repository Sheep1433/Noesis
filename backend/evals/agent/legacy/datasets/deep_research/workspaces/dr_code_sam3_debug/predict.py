"""简化版文本分类预测（含 Bug）。"""

import json
from pathlib import Path

CASES = {
    "text_shoe": ["red shoe", "blue shoe", "sandal"],
    "text_hat": ["cap", "hat", "helmet"],
}


def predict(label: str, samples: list[str]) -> float:
    """返回 precision。BUG: 始终返回 0.0。"""
    _ = label
    _ = samples
    return 0.0


def main() -> None:
    results = {"cases": {}}
    for name, samples in CASES.items():
        label = name.split("_", 1)[-1]
        results["cases"][name] = {"precision": predict(label, samples)}
    out = Path("results")
    out.mkdir(parents=True, exist_ok=True)
    (out / "predictions.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
