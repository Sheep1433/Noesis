"""Load versioned memory fixtures without model or external-service calls."""

from __future__ import annotations

import json
from pathlib import Path

from evals.memory_cortex.schema import RunMemoryFixture


FIXTURE_ROOT = Path(__file__).with_name("fixtures")


def load_fixtures(split: str | None = None) -> list[RunMemoryFixture]:
    config = json.loads((FIXTURE_ROOT.parent / "eval_config.json").read_text(encoding="utf-8"))
    test_file = str(config.get("test_fixture_file") or "test.json")
    paths = (
        [FIXTURE_ROOT / (test_file if split == "test" else f"{split}.json")]
        if split
        else [FIXTURE_ROOT / "dev.json", FIXTURE_ROOT / test_file]
    )
    fixtures: list[RunMemoryFixture] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fixtures.extend(RunMemoryFixture.model_validate(item) for item in payload)
    ids = [fixture.id for fixture in fixtures]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate memory fixture id")
    return fixtures


__all__ = ["FIXTURE_ROOT", "load_fixtures"]
