"""记忆召回行为评测 runner：LongMemEval 三层指标 + 自建配对负例。

三层：① 答案正确性（judge 对 gold answer 判卷）② 检索命中（search_memory
返回条目对 answer_session_ids 的 recall@k / precision@k）③ 行为级召回
（是否主动调用 search_memory）。负例断言：无记忆线索的提问不误召回。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from evals.agent._agent import run_super_agent
from evals.agent.memory.fixtures import EVAL_USER_ID, SEEDED_ENTRIES
from evals.agent.memory.longmemeval import eval_user_id, import_question
from evals.agent.memory.metrics import (
    memory_accessed,
    parse_search_memory_slugs,
    retrieval_scores,
)
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


def run_longmemeval_positive(
    question: dict[str, Any],
    *,
    model_id: str | None = None,
    time_budget_seconds: int = 240,
) -> dict[str, Any]:
    """跑一题 LongMemEval 正例：导入 haystack → SuperAgent 提问 → 采集三层原始数据。"""
    sample_id = str(question["question_id"])
    user_id = import_question(question)
    started = time.perf_counter()
    result = run_super_agent(
        query=str(question["question"]).strip(),
        session_id=f"eval-lme-{sample_id}-{uuid.uuid4().hex[:8]}",
        user_id=user_id,
        time_budget_seconds=time_budget_seconds,
        model_id=model_id,
    )
    tool_stats: dict[str, int] = result.get("tool_stats") or {}
    returned = parse_search_memory_slugs(result.get("tool_outputs") or [])
    retrieval = retrieval_scores(returned, question.get("answer_session_ids") or [])
    return {
        "sample_id": sample_id,
        "negative": False,
        "user_id": user_id,
        "question": question["question"],
        "answer": question.get("answer") or "",
        "question_type": question.get("question_type") or "",
        "answer_session_ids": question.get("answer_session_ids") or [],
        "completed": bool(result.get("completed")),
        "error": result.get("error"),
        "final_text": str(result.get("final_text") or ""),
        "tool_stats": tool_stats,
        "input_tokens": result.get("input_tokens") or 0,
        "output_tokens": result.get("output_tokens") or 0,
        "latency_ms": result.get("latency_ms") or 0,
        # 层 3：行为级（主动访问记忆：search_memory 或 /memory 路径读取）
        "search_memory_calls": tool_stats.get("search_memory", 0),
        "memory_accessed": memory_accessed({
            "search_memory_calls": tool_stats.get("search_memory", 0),
            "tool_outputs": result.get("tool_outputs") or [],
        }),
        # 层 2：条目级
        "retrieval": retrieval,
        # 层 1（judge 在 CLI 层补，保持 runner 无 LLM 依赖）
    }


def run_negative_sample(
    *,
    user_id: str,
    query: str,
    model_id: str | None = None,
    time_budget_seconds: int = 240,
    forbidden_snippets: list[str] | None = None,
    sample_id: str | None = None,
) -> dict[str, Any]:
    """跑一条负例：无记忆线索的提问 → 断言未调用 search_memory 且未引用种子事实。

    forbidden_snippets：种子正文中可字面检测的特征句（LongMemEval 长会话无法
    字面检测，仅行为断言）。
    """
    sid = sample_id or f"neg-{uuid.uuid4().hex[:8]}"
    result = run_super_agent(
        query=query,
        session_id=f"eval-lme-neg-{sid}-{uuid.uuid4().hex[:8]}",
        user_id=user_id,
        time_budget_seconds=time_budget_seconds,
        model_id=model_id,
    )
    tool_stats: dict[str, int] = result.get("tool_stats") or {}
    tool_outputs = result.get("tool_outputs") or []
    final_text = str(result.get("final_text") or "")
    record = {
        "sample_id": sid,
        "negative": True,
        "user_id": user_id,
        "query": query,
        "completed": bool(result.get("completed")),
        "error": result.get("error"),
        "final_text": final_text,
        "tool_stats": tool_stats,
        "tool_outputs": tool_outputs,
        "input_tokens": result.get("input_tokens") or 0,
        "output_tokens": result.get("output_tokens") or 0,
        "search_memory_calls": tool_stats.get("search_memory", 0),
        "memory_accessed": memory_accessed({
            "search_memory_calls": tool_stats.get("search_memory", 0),
            "tool_outputs": tool_outputs,
        }),
    }
    leaked = any(s and s in final_text for s in (forbidden_snippets or []))
    record["violation"] = record["memory_accessed"] or leaked
    record["violation_reason"] = (
        "accessed memory" if record["memory_accessed"] else
        "seed fact leaked" if leaked else None)
    return record


def run_memory_recall_sample(
    scenario: dict[str, Any],
    *,
    user_id: str = EVAL_USER_ID,
    time_budget_seconds: int = 240,
    model_id: str | None = None,
) -> dict[str, Any]:
    """冒烟模式：应召回场景 → 断言 Agent 经 search_memory 主动检索（旧四场景保留）。"""
    sample_id = str(scenario.get("id") or uuid.uuid4().hex[:12])
    query = str(scenario["query"]).strip()
    result = run_super_agent(
        query=query,
        session_id=f"eval-memory-recall-{sample_id}-{uuid.uuid4().hex[:8]}",
        user_id=user_id,
        time_budget_seconds=time_budget_seconds,
        model_id=model_id,
    )
    tool_stats: dict[str, int] = result.get("tool_stats") or {}
    search_calls = tool_stats.get("search_memory", 0)
    final_text = str(result.get("final_text") or "")
    expect_label = str(scenario.get("expect_label") or "")
    return {
        "sample_id": sample_id,
        "negative": False,
        "user_id": user_id,
        "query": query,
        "expect_label": expect_label,
        "completed": bool(result.get("completed")),
        "error": result.get("error"),
        "final_text": final_text,
        "tool_stats": tool_stats,
        "tool_outputs": result.get("tool_outputs") or [],
        "input_tokens": result.get("input_tokens") or 0,
        "output_tokens": result.get("output_tokens") or 0,
        "search_memory_calls": search_calls,
        "memory_accessed": memory_accessed({
            "search_memory_calls": search_calls,
            "tool_outputs": result.get("tool_outputs") or [],
        }),
        "recalled": memory_accessed({
            "search_memory_calls": search_calls,
            "tool_outputs": result.get("tool_outputs") or [],
        }),
        # label 在回答中出现（字面检测；正式三层指标见 longmemeval 路径）
        "expect_label_surfaced": (not expect_label) or (expect_label in final_text),
    }


__all__ = [
    "EVAL_USER_ID",
    "run_longmemeval_positive",
    "run_memory_recall_sample",
    "run_negative_sample",
    "seed_eval_memory",
]
