"""记忆召回行为评测 runner：应召回场景 → Agent 调用 search_memory。"""

from __future__ import annotations

import time
import uuid
from typing import Any

from evals.agent._agent import run_super_agent
from evals.agent.memory.fixtures import EVAL_USER_ID, RECALL_SCENARIOS, SEEDED_ENTRIES
from noesis.services.memory.store import MemoryStore


def seed_eval_memory(user_id: str = EVAL_USER_ID) -> list[str]:
    """幂等写入评测用户记忆种子；返回条目相对路径。"""
    paths: list[str] = []
    for entry in SEEDED_ENTRIES:
        result = MemoryStore.upsert_entry(
            user_id,
            memory_type=entry["memory_type"],
            label=entry["label"],
            body=entry["body"],
            why=entry.get("why", ""),
            applicability=entry.get("applicability", ""),
            description=entry["description"],
            sources=[f"评测种子 · {time.strftime('%Y-%m-%d')}"],
            slug=entry["slug_hint"],
        )
        paths.append(result.rel_path)
    return paths


def run_memory_recall_sample(
    scenario: dict[str, Any],
    *,
    user_id: str = EVAL_USER_ID,
    time_budget_seconds: int = 240,
    model_id: str | None = None,
) -> dict[str, Any]:
    """跑单个应召回场景：断言 Agent 经 search_memory 主动检索。"""
    sample_id = str(scenario.get("id") or uuid.uuid4().hex[:12])
    query = str(scenario["query"]).strip()
    session_id = f"eval-memory-recall-{sample_id}-{uuid.uuid4().hex[:8]}"
    started = time.perf_counter()
    result = run_super_agent(
        query=query,
        session_id=session_id,
        user_id=user_id,
        time_budget_seconds=time_budget_seconds,
        model_id=model_id,
    )
    tool_stats: dict[str, int] = result.get("tool_stats") or {}
    search_calls = tool_stats.get("search_memory", 0)
    final_text = str(result.get("final_text") or "")
    expect_label = str(scenario.get("expect_label") or "")
    return {
        "schema_version": "noesis-memory-recall/v1",
        "sample_id": sample_id,
        "query": query,
        "expect_label": expect_label,
        "search_memory_calls": search_calls,
        # 应召回场景的行为断言：Agent 先检索再产出
        "recalled": search_calls > 0,
        "expect_label_surfaced": (not expect_label) or (expect_label in final_text),
        "completed": bool(result.get("completed")),
        "error": result.get("error"),
        "tool_stats": tool_stats,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


__all__ = ["RECALL_SCENARIOS", "run_memory_recall_sample", "seed_eval_memory"]
