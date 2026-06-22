"""DeepResearchAgent 离线 runner。"""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from agent.deep_research_agent import DeepResearchAgent
from config.agent_workspace_paths import ensure_workspace_dir, get_workspace_dir
from evals.agent.dataset import resolve_workspace_seed

EVAL_USER_ID = "eval"
DEFAULT_TIME_BUDGET_SECONDS = 600


@dataclass
class StreamCollector:
    tool_stats: Dict[str, int] = field(default_factory=dict)
    text_parts: List[str] = field(default_factory=list)
    completed: bool = False
    finish_reason: Optional[str] = None
    error: Optional[str] = None

    def consume(self, chunk: Dict[str, Any]) -> None:
        chunk_type = chunk.get("type")
        if chunk_type == "__tw_finish__":
            self.finish_reason = chunk.get("finish_reason")
            self.completed = self.finish_reason == "stop"
            return
        if chunk_type == "__tw_error__":
            self.error = str(chunk.get("content") or "agent error")
            self.completed = False
            return
        if chunk_type in ("abort", "__tw_abort__"):
            self.error = str(chunk.get("content") or "aborted")
            self.completed = False
            return

        event = chunk.get("event")
        if event == "on_tool_start":
            name = str(chunk.get("name") or "unknown")
            self.tool_stats[name] = self.tool_stats.get(name, 0) + 1
            return

        if event == "on_chat_model_stream":
            data = chunk.get("data") or {}
            stream_chunk = data.get("chunk")
            if stream_chunk is None:
                return
            content = getattr(stream_chunk, "content", None)
            if content:
                self.text_parts.append(str(content))

    @property
    def final_text(self) -> str:
        return "".join(self.text_parts).strip()


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


async def _run_agent_async(
    *,
    query: str,
    session_id: str,
    time_budget_seconds: int,
) -> StreamCollector:
    agent = DeepResearchAgent()
    current_user = SimpleNamespace(user_id=EVAL_USER_ID)
    collector = StreamCollector()

    async def _consume() -> None:
        async for chunk in agent.run_agent(
            query,
            session_id=session_id,
            current_user=current_user,
            qa_type="DEEP_RESEARCH_QA",
        ):
            collector.consume(chunk)

    try:
        await asyncio.wait_for(_consume(), timeout=time_budget_seconds)
    except asyncio.TimeoutError:
        await agent.cancel_task(session_id)
        collector.error = f"timeout after {time_budget_seconds}s"
        collector.completed = False

    return collector


def run_agent_item(
    item: Dict[str, Any],
    *,
    dataset_dir: Path,
    eval_run_id: str,
) -> Dict[str, Any]:
    item_id = item["id"]
    run_id = uuid.uuid4().hex[:12]
    session_id = _eval_session_id(item_id, run_id)
    time_budget = int(item.get("time_budget_seconds") or DEFAULT_TIME_BUDGET_SECONDS)

    seed_path = resolve_workspace_seed(item, dataset_dir)
    workspace_dir = ensure_workspace_dir(EVAL_USER_ID, session_id)
    if seed_path is not None:
        seed_workspace(seed_path, workspace_dir)

    t0 = time.perf_counter()
    collector = asyncio.run(
        _run_agent_async(
            query=str(item["query"]).strip(),
            session_id=session_id,
            time_budget_seconds=time_budget,
        )
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)

    return {
        "dataset_item_id": item_id,
        "eval_run_id": eval_run_id,
        "run_id": run_id,
        "session_id": session_id,
        "workspace_path": str(workspace_dir),
        "query": item["query"],
        "category": item.get("category"),
        "provenance": item.get("provenance"),
        "completed": collector.completed,
        "finish_reason": collector.finish_reason,
        "error": collector.error,
        "latency_ms": latency_ms,
        "final_text": collector.final_text,
        "tool_stats": collector.tool_stats,
        "time_budget_seconds": time_budget,
    }


def workspace_path_for_item(item_id: str, run_id: str) -> Path:
    return get_workspace_dir(EVAL_USER_ID, _eval_session_id(item_id, run_id))
