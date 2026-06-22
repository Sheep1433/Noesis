"""Fixture 与 probe 加载。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

COMPRESSION_ROOT = Path(__file__).resolve().parent
FIXTURES_DIR = COMPRESSION_ROOT / "fixtures"
PROBES_DIR = COMPRESSION_ROOT / "probes"

PROBE_TYPES = frozenset({"recall", "artifact", "continuation", "decision"})


def list_fixture_ids() -> List[str]:
    return sorted(p.stem for p in FIXTURES_DIR.glob("*.json"))


def load_fixture(fixture_id: str) -> Dict[str, Any]:
    path = FIXTURES_DIR / f"{fixture_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"fixture 不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("id") != fixture_id:
        raise ValueError(f"fixture id 不匹配: {path}")
    if not isinstance(data.get("messages"), list) or not data["messages"]:
        raise ValueError(f"fixture messages 为空: {path}")
    return data


def load_probes(fixture_id: str) -> Dict[str, Any]:
    path = PROBES_DIR / f"{fixture_id}.probes.json"
    if not path.is_file():
        raise FileNotFoundError(f"probe 文件不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("fixture_id") != fixture_id:
        raise ValueError(f"probe fixture_id 不匹配: {path}")
    probes = data.get("probes")
    if not isinstance(probes, list) or not probes:
        raise ValueError(f"probes 为空: {path}")
    for probe in probes:
        if probe.get("type") not in PROBE_TYPES:
            raise ValueError(f"非法 probe type: {probe.get('type')}")
        for key in ("id", "question", "reference_answer"):
            if not str(probe.get(key) or "").strip():
                raise ValueError(f"probe 缺少 {key}: {probe.get('id')}")
    return data


def filter_fixtures(
    fixture_ids: List[str],
    *,
    fixture: Optional[str] = None,
) -> List[str]:
    if fixture:
        if fixture not in fixture_ids:
            raise ValueError(f"未找到 fixture={fixture!r}")
        return [fixture]
    return fixture_ids
