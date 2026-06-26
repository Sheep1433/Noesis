"""DeepResearchAgent 执行（各 benchmark 共用）。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from agent.deep_research_agent import DeepResearchAgent
from config.agent_workspace_paths import ensure_workspace_dir
from evals.bootstrap import eval_runtime

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


async def _run_async(
    *,
    query: str,
    session_id: str,
    user_id: str,
    time_budget_seconds: int,
) -> StreamCollector:
    ensure_workspace_dir(user_id, session_id)
    async with eval_runtime():
        agent = DeepResearchAgent()
        user = SimpleNamespace(user_id=user_id)
        collector = StreamCollector()

        async def consume() -> None:
            async for chunk in agent.run_agent(
                query,
                session_id=session_id,
                current_user=user,
                qa_type="DEEP_RESEARCH_QA",
            ):
                collector.consume(chunk)

        try:
            await asyncio.wait_for(consume(), timeout=time_budget_seconds)
        except asyncio.TimeoutError:
            await agent.cancel_task(session_id)
            collector.error = f"timeout after {time_budget_seconds}s"
            collector.completed = False
    return collector


def run_deep_research(
    *,
    query: str,
    session_id: str,
    user_id: str = "eval",
    time_budget_seconds: int = DEFAULT_TIME_BUDGET_SECONDS,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    collector = asyncio.run(
        _run_async(
            query=query.strip(),
            session_id=session_id,
            user_id=user_id,
            time_budget_seconds=time_budget_seconds,
        )
    )
    return {
        "session_id": session_id,
        "completed": collector.completed,
        "finish_reason": collector.finish_reason,
        "error": collector.error,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "final_text": collector.final_text,
        "tool_stats": collector.tool_stats,
    }
