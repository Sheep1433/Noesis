"""评测 runner 基础：数据集加载、run 目录、agent 路由。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

EVALS_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = EVALS_ROOT / "datasets" / "test_case" / "dataset.jsonl"
REPO_ROOT = EVALS_ROOT.parent.parent
DEFAULT_RESULTS_ROOT = REPO_ROOT / "results"


class EvalRunnerError(Exception):
    """评测 runner 业务错误（如未知 agent_type）。"""


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
            items.append(item)

    ids = [i["id"] for i in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"数据集 id 重复: {dataset_path}")

    return items


def resolve_document_context(item: Dict[str, Any], dataset_dir: Path) -> str:
    if item.get("document_context"):
        return str(item["document_context"])
    doc_path = item.get("document_path")
    if not doc_path:
        return ""
    full = (dataset_dir / doc_path).resolve()
    if not full.is_file():
        raise FileNotFoundError(f"文档不存在: {full}")
    return full.read_text(encoding="utf-8")


def resolve_run_dir(
    tag: str,
    results_root: Optional[Path] = None,
) -> Path:
    root = results_root or DEFAULT_RESULTS_ROOT
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{tag}_{uuid.uuid4().hex[:8]}"
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def get_runner(agent_type: str):
    if agent_type in ("test_case", "test-case"):
        from evals.runners.test_case import run_test_case_item

        return run_test_case_item
    raise EvalRunnerError(f"未实现的 agent_type: {agent_type}")
