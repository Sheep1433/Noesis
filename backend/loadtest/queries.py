"""深度研究压测用查询集。"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Iterable

DEFAULT_DATASET = (
    Path(__file__).resolve().parent.parent
    / "evals"
    / "agent"
    / "datasets"
    / "deep_research"
    / "dataset.jsonl"
)

# 快速冒烟：只触发检索 + 落盘，通常比完整 7 阶段报告快
SMOKE_QUERIES: tuple[str, ...] = (
    "请检索 HTTP/1.1 与 HTTP/2 的公开技术资料，对比连接复用与头部压缩差异，"
    "将 3 条要点写入 output/protocol_notes.md。",
    "针对「开源向量数据库」主题，在工作区撰写 reports/market_scan.md："
    "包含市场概述、至少 2 个代表性产品与各自优缺点。",
)


def load_dataset_queries(path: Path | None = None) -> list[str]:
    dataset_path = path or DEFAULT_DATASET
    if not dataset_path.is_file():
        return list(SMOKE_QUERIES)

    queries: list[str] = []
    for line in dataset_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        query = str(item.get("query") or "").strip()
        if query:
            queries.append(query)
    return queries or list(SMOKE_QUERIES)


def pick_query(queries: Iterable[str]) -> str:
    pool = list(queries)
    if not pool:
        return SMOKE_QUERIES[0]
    return random.choice(pool)
