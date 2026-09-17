"""Run GeneralQAAgent（common 场景）经 CLI 子进程驱动真实生产链路。

与 deepresearch / memory 线同构（cli_driver 契约）：每题一个 noesis CLI
子进程，走生产 headless 入口——消息/run/token 落库到 `--eval-user` 账号
（前端可查），stdout 为生产同源 SSE 流。产物只做采集不做判分：质量指标
全部由 ERB 官方判分脚本产出（见 to_erb.py 与 evals/README）。
"""

from __future__ import annotations

import uuid
from typing import Any

from evals.agent.cli_driver import run_cli_agent


async def run_agentic_rag_sample(
    sample: dict[str, Any],
    *,
    time_budget_seconds: int = 180,
    model_id: str | None = None,
    eval_user: str = "test",
) -> dict[str, Any]:
    query = str(sample.get("query") or "").strip()
    if not query:
        raise ValueError("Agentic RAG sample requires query")
    sample_id = str(sample.get("id") or uuid.uuid4().hex[:12])
    session_id = f"eval-agentic-rag-{sample_id}-{uuid.uuid4().hex[:8]}"

    record = await run_cli_agent(
        query=query,
        session_id=session_id,
        user_id=eval_user,
        model=model_id,
        qa_type="common",
        kb_collections=[c for c in (sample.get("collection_names") or []) if c],
        web_search=False,
        time_budget_seconds=time_budget_seconds,
    )

    usage = record.get("session_usage") or {}
    # token 计数在 __tw_result__ 顶层（stats-update 未出现时兜底）
    input_tokens = int(
        record.get("input_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(
        record.get("output_tokens") or usage.get("output_tokens") or 0)
    result: dict[str, Any] = {
        "sample_id": sample_id,
        "completed": record.get("completed"),
        "error": record.get("error"),
        "final_text": record.get("final_text") or "",
        "tool_stats": record.get("tool_stats") or {},
        "tool_outputs": record.get("tool_outputs") or [],
        "latency_ms": record.get("latency_ms") or 0,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "session_id": session_id,
    }
    return result
