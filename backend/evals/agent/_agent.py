"""SuperAgent 执行（各 benchmark 共用）。"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any, Dict

from noesis.agents.super_agent import SuperAgent
from noesis.config.user_data_paths import ensure_workspace_dir
from evals.bootstrap import eval_runtime
from evals.agent.runtime import AgentEventCollector

DEFAULT_TIME_BUDGET_SECONDS = 600


async def _run_async(
    *,
    query: str,
    session_id: str,
    user_id: str,
    time_budget_seconds: int,
) -> AgentEventCollector:
    ensure_workspace_dir(user_id, session_id)
    async with eval_runtime(no_attachments=True):
        agent = SuperAgent()
        user = SimpleNamespace(user_id=user_id)
        collector = AgentEventCollector()

        async def consume() -> None:
            async for chunk in agent.run_agent(
                query,
                session_id=session_id,
                current_user=user,
                qa_type="SUPER_AGENT_QA",
            ):
                collector.consume(chunk)

        try:
            await asyncio.wait_for(consume(), timeout=time_budget_seconds)
        except asyncio.TimeoutError:
            await agent.cancel_task(session_id)
            collector.error = f"timeout after {time_budget_seconds}s"
            collector.completed = False
    return collector


def run_super_agent(
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
    result = collector.result(
        run_id=session_id,
        suite="browsecomp",
        subject="super-agent",
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )
    payload = result.to_dict()
    payload["session_id"] = session_id
    return payload
