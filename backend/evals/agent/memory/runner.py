"""记忆召回行为评测 runner：LongMemEval 三层指标 + 自建配对负例。

三层：① 答案正确性（judge 对 gold answer 判卷）② 检索命中（search_memory
返回条目对 answer_session_ids 的 recall@k / precision@k）③ 行为级召回
（是否主动调用 search_memory）。负例断言：无记忆线索的提问不误召回。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from evals.agent.cli_driver import run_cli_agent
from evals.agent.memory.fixtures import EVAL_USER_ID, SEEDED_ENTRIES
from evals.agent.memory.longmemeval import import_question
from evals.agent.memory.metrics import (
    memory_accessed,
    parse_search_memory_slugs,
    retrieval_scores,
)
from noesis.memory.store import MemoryStore

# 子 Agent 前台等待窗口：生产默认 600s 超时即自动转后台，单回合评测没有
# 通知回合，主 Agent 只能轮询收结果（v3 基线实测单题轮询 46 次）；
# 显式注入固定窗口，避免默认值变动波及评测（deepresearch 线同款处理）
_SUBAGENT_ENV = {"SUBAGENT_FOREGROUND_MAX_WAIT_SECONDS": "600"}

# 评测模式说明（同 deepresearch 线）：单回合运行没有生产环境的后台任务
# 通知回合，不附加则 Agent 可能委派后台子 Agent 后直接结束回合，交付物
# 变成「已转入后台」的过程叙述（基线 7024f17c 实测失败模式）。
# 仅附加在正例问题上；负例保持原始提问，不污染行为测量。
EVAL_MODE_SUFFIX = (
    "\n\n（评测模式说明：本次运行为单回合评测，不存在后台任务完成后的通知回合。"
    "你必须在本次运行结束前给出最终答案。"
    "如需子 Agent，一律 run_in_background=false 前台等待其完成后继续；"
    "不要把任务留在后台轮询，不要把「已转入后台/稍后汇总」作为最终输出。）"
)


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


async def run_longmemeval_positive(
    question: dict[str, Any],
    *,
    model_id: str | None = None,
    time_budget_seconds: int = 600,
) -> dict[str, Any]:
    """跑一题 LongMemEval 正例：导入 haystack → SuperAgent 提问 → 采集三层原始数据。"""
    sample_id = str(question["question_id"])
    user_id = import_question(question)
    # session id 须 ≤36 字符（t_chat_session.id VARCHAR(36)）：前缀 + uuid4 hex
    session_id = f"lme-{uuid.uuid4().hex}"
    result = await run_cli_agent(
        query=str(question["question"]).strip() + EVAL_MODE_SUFFIX,
        session_id=session_id,
        user_id=user_id,
        time_budget_seconds=time_budget_seconds,
        model=model_id,
        extra_env=_SUBAGENT_ENV,
    )
    tool_stats: dict[str, int] = result.get("tool_stats") or {}
    returned = parse_search_memory_slugs(result.get("tool_outputs") or [])
    retrieval = retrieval_scores(returned, question.get("answer_session_ids") or [])
    return {
        "sample_id": sample_id,
        "negative": False,
        "user_id": user_id,
        "session_id": session_id,
        "question": question["question"],
        "answer": question.get("answer") or "",
        "question_type": question.get("question_type") or "",
        "answer_session_ids": question.get("answer_session_ids") or [],
        "completed": bool(result.get("completed")),
        "error": result.get("error"),
        "final_text": str(result.get("final_text") or ""),
        "tool_stats": tool_stats,
        "tool_outputs": result.get("tool_outputs") or [],
        "input_tokens": result.get("input_tokens") or 0,
        "output_tokens": result.get("output_tokens") or 0,
        "latency_ms": result.get("latency_ms") or 0,
        # CLI 子进程诊断（error 时归因）：退出码与 stderr 尾部
        "cli_exit_code": result.get("cli_exit_code"),
        "stderr_tail": result.get("stderr_tail"),
        # 事后归因用：error 两字 summary 不够时看 finish_reason/outcome 原始字段
        "finish_reason": result.get("finish_reason"),
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


async def run_negative_sample(
    *,
    user_id: str,
    query: str,
    model_id: str | None = None,
    time_budget_seconds: int = 600,
    forbidden_snippets: list[str] | None = None,
    sample_id: str | None = None,
) -> dict[str, Any]:
    """跑一条负例：无记忆线索的提问 → 断言未调用 search_memory 且未引用种子事实。

    forbidden_snippets：种子正文中可字面检测的特征句（LongMemEval 长会话无法
    字面检测，仅行为断言）。
    """
    sid = sample_id or f"neg-{uuid.uuid4().hex[:8]}"
    session_id = f"neg-{uuid.uuid4().hex}"
    result = await run_cli_agent(
        query=query,
        session_id=session_id,
        user_id=user_id,
        time_budget_seconds=time_budget_seconds,
        model=model_id,
        extra_env=_SUBAGENT_ENV,
    )
    tool_stats: dict[str, int] = result.get("tool_stats") or {}
    tool_outputs = result.get("tool_outputs") or []
    final_text = str(result.get("final_text") or "")
    record = {
        "sample_id": sid,
        "negative": True,
        "user_id": user_id,
        "session_id": session_id,
        "query": query,
        "completed": bool(result.get("completed")),
        "error": result.get("error"),
        "final_text": final_text,
        "tool_stats": tool_stats,
        "tool_outputs": tool_outputs,
        "input_tokens": result.get("input_tokens") or 0,
        "output_tokens": result.get("output_tokens") or 0,
        "finish_reason": result.get("finish_reason"),
        "cli_exit_code": result.get("cli_exit_code"),
        "stderr_tail": result.get("stderr_tail"),
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


async def run_memory_recall_sample(
    scenario: dict[str, Any],
    *,
    user_id: str = EVAL_USER_ID,
    time_budget_seconds: int = 600,
    model_id: str | None = None,
) -> dict[str, Any]:
    """冒烟模式：应召回场景 → 断言 Agent 经 search_memory 主动检索（旧四场景保留）。"""
    sample_id = str(scenario.get("id") or uuid.uuid4().hex[:12])
    query = str(scenario["query"]).strip()
    session_id = f"smk-{uuid.uuid4().hex}"
    result = await run_cli_agent(
        query=query + EVAL_MODE_SUFFIX,
        session_id=session_id,
        user_id=user_id,
        time_budget_seconds=time_budget_seconds,
        model=model_id,
        extra_env=_SUBAGENT_ENV,
    )
    tool_stats: dict[str, int] = result.get("tool_stats") or {}
    search_calls = tool_stats.get("search_memory", 0)
    final_text = str(result.get("final_text") or "")
    expect_label = str(scenario.get("expect_label") or "")
    return {
        "sample_id": sample_id,
        "negative": False,
        "user_id": user_id,
        "session_id": session_id,
        "query": query,
        "expect_label": expect_label,
        "completed": bool(result.get("completed")),
        "error": result.get("error"),
        "final_text": final_text,
        "tool_stats": tool_stats,
        "tool_outputs": result.get("tool_outputs") or [],
        "input_tokens": result.get("input_tokens") or 0,
        "output_tokens": result.get("output_tokens") or 0,
        "finish_reason": result.get("finish_reason"),
        "cli_exit_code": result.get("cli_exit_code"),
        "stderr_tail": result.get("stderr_tail"),
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
