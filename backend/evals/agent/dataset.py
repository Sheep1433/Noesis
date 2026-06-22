"""深度研究 Agent 评测数据集加载。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

AGENT_ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET = AGENT_ROOT / "datasets" / "deep_research" / "dataset.jsonl"
DEFAULT_DATASET_DIR = DEFAULT_DATASET.parent


def load_dataset(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    dataset_path = path or DEFAULT_DATASET
    if not dataset_path.is_file():
        raise FileNotFoundError(f"数据集不存在: {dataset_path}")

    items: List[Dict[str, Any]] = []
    with dataset_path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{dataset_path}:{line_no} JSON 解析失败: {e}") from e
            if not item.get("id"):
                raise ValueError(f"{dataset_path}:{line_no} 缺少 id")
            if not str(item.get("query") or "").strip():
                raise ValueError(f"{dataset_path}:{line_no} 缺少 query")
            items.append(item)

    ids = [i["id"] for i in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"数据集 id 重复: {dataset_path}")

    return items


def resolve_workspace_seed(item: Dict[str, Any], dataset_dir: Path) -> Optional[Path]:
    seed_rel = item.get("workspace_seed")
    if not seed_rel:
        return None
    seed_path = (dataset_dir / str(seed_rel)).resolve()
    if not seed_path.is_dir():
        raise FileNotFoundError(f"workspace_seed 不存在: {seed_path}")
    return seed_path


def filter_items(
    items: List[Dict[str, Any]],
    *,
    item_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    out = items
    if item_id:
        out = [i for i in out if i["id"] == item_id]
        if not out:
            raise ValueError(f"未找到 item_id={item_id!r}")
    if limit is not None:
        out = out[:limit]
    return out
