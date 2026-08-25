"""CLI entry for frozen memory fixtures; live pipeline wiring is added later."""

from __future__ import annotations

import argparse
import json

from evals.memory_cortex.loader import load_fixtures


def fixture_manifest(split: str | None = None) -> dict[str, object]:
    fixtures = load_fixtures(split)
    return {
        "fixtures": len(fixtures),
        "capture_positive": sum(item.expected_capture for item in fixtures),
        "no_output": sum(item.expected_no_output for item in fixtures),
        "categories": sorted({item.category for item in fixtures}),
        "memory_types": sorted({gold.memory_type.value for item in fixtures for gold in item.gold_items}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect frozen machine-memory eval fixtures")
    parser.add_argument("--split", choices=("dev", "test"))
    args = parser.parse_args()
    print(json.dumps(fixture_manifest(args.split), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
