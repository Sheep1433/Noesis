"""深度研究压测查询集（evals/loadtest/data/queries.jsonl）。"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Iterable

DATASET = Path(__file__).resolve().parent / "data" / "queries.jsonl"


def load_dataset_queries(path: Path | None = None) -> list[str]:
    dataset_path = path or DATASET
    if not dataset_path.is_file():
        raise FileNotFoundError(f"压测数据集不存在: {dataset_path}")

    queries: list[str] = []
    for line in dataset_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        query = str(item.get("query") or "").strip()
        if query:
            queries.append(query)
    if not queries:
        raise ValueError(f"压测数据集为空: {dataset_path}")
    return queries


def pick_query(queries: Iterable[str]) -> str:
    pool = list(queries)
    return random.choice(pool)
