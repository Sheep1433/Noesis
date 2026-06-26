"""DeepResearchAgent 离线 runner（legacy benchmark 路径）。"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any, Dict

from config.agent_workspace_paths import ensure_workspace_dir, get_workspace_dir
from evals.agent._agent import run_deep_research
from evals.agent.legacy.dataset import resolve_workspace_seed

EVAL_USER_ID = "eval"
DEFAULT_TIME_BUDGET_SECONDS = 600


def _eval_session_id(item_id: str, run_id: str) -> str:
    return f"eval-{item_id}-{run_id}"


def seed_workspace(seed_dir: Path, workspace_dir: Path) -> None:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    for entry in seed_dir.iterdir():
        dest = workspace_dir / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(entry, dest)


def run_agent_item(
    item: Dict[str, Any],
    *,
    dataset_dir: Path,
    eval_run_id: str,
    run_id: str | None = None,
) -> Dict[str, Any]:
    item_id = item["id"]
    run_id = run_id or uuid.uuid4().hex[:12]
    session_id = _eval_session_id(item_id, run_id)
    time_budget = int(item.get("time_budget_seconds") or DEFAULT_TIME_BUDGET_SECONDS)

    seed_path = resolve_workspace_seed(item, dataset_dir)
    workspace_dir = ensure_workspace_dir(EVAL_USER_ID, session_id)
    if seed_path is not None:
        seed_workspace(seed_path, workspace_dir)

    run = run_deep_research(
        query=str(item["query"]).strip(),
        session_id=session_id,
        user_id=EVAL_USER_ID,
        time_budget_seconds=time_budget,
    )

    return {
        "dataset_item_id": item_id,
        "eval_run_id": eval_run_id,
        "run_id": run_id,
        "session_id": session_id,
        "workspace_path": str(workspace_dir),
        "query": item["query"],
        "category": item.get("category"),
        "provenance": item.get("provenance"),
        "completed": run.get("completed"),
        "finish_reason": run.get("finish_reason"),
        "error": run.get("error"),
        "latency_ms": run.get("latency_ms"),
        "final_text": run.get("final_text"),
        "tool_stats": run.get("tool_stats"),
        "time_budget_seconds": time_budget,
    }


def workspace_path_for_item(item_id: str, run_id: str) -> Path:
    return get_workspace_dir(EVAL_USER_ID, _eval_session_id(item_id, run_id))
