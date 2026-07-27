"""Run GeneralQAAgent through the real Harness KB Tool chain."""

from __future__ import annotations

import asyncio
import time
import uuid
from types import SimpleNamespace
from typing import Any

from evals.agent.rag.scoring import score_expected_sources
from evals.agent.runtime import AgentEventCollector
from evals.bootstrap import eval_runtime
from noesis.agents.common_qa import GeneralQAAgent


async def run_agentic_rag_sample(
    sample: dict[str, Any],
    *,
    time_budget_seconds: int = 180,
    model_id: str | None = None,
) -> dict[str, Any]:
    query = str(sample.get("query") or "").strip()
    if not query:
        raise ValueError("Agentic RAG sample requires query")
    sample_id = str(sample.get("id") or uuid.uuid4().hex[:12])
    session_id = f"eval-agentic-rag-{sample_id}-{uuid.uuid4().hex[:8]}"
    collector = AgentEventCollector()
    agent = GeneralQAAgent()
    started = time.perf_counter()

    async with eval_runtime(no_attachments=True):
        async def consume() -> None:
            async for event in agent.run_agent(
                query,
                session_id=session_id,
                current_user=SimpleNamespace(user_id="eval-agentic-rag"),
                qa_type="COMMON_QA",
                kb_collections=list(sample.get("collection_names") or []),
                kb_search_enabled=True,
                web_search_enabled=False,
                model_id=model_id,
            ):
                collector.consume(event)

        try:
            await asyncio.wait_for(consume(), timeout=time_budget_seconds)
        except asyncio.TimeoutError:
            await agent.cancel_task(session_id)
            collector.error = f"timeout after {time_budget_seconds}s"

    result = collector.result(
        run_id=session_id,
        suite="agentic-rag",
        subject="general-qa-agent",
        model=model_id,
        latency_ms=int((time.perf_counter() - started) * 1000),
    ).to_manifest()
    result["sample_id"] = sample_id
    result["kb_tool_called"] = collector.tool_stats.get("search_knowledge_base", 0) > 0
    result["source_score"] = score_expected_sources(
        collector.tool_outputs, sample.get("expected_sources") or []
    )
    return result
