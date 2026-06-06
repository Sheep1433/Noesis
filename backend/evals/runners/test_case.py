"""测试用例 Agent 离线 runner（直接调用 case_graph 节点）。"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Literal, Optional

from langgraph.types import Command

from agent.case_generate.case_graph import (
    TestCaseState,
    generate_scenes_testpoints_node,
    generate_test_cases_node,
)
from config.env import LangfuseConfig
from evals.item_fields import collect_all_point_names, item_scenario_query
from evals.runners.base import resolve_document_context

EvalScope = Literal["testpoints", "cases", "full"]


def _merge_command(state: Dict[str, Any], command: Command) -> Dict[str, Any]:
    out = dict(state)
    update = getattr(command, "update", None) or {}
    out.update(update)
    return out


def _initial_state(query: str, document_context: str, source_file_names: Optional[List[str]] = None) -> TestCaseState:
    return {
        "query": query,
        "document_context": document_context,
        "source_file_names": list(source_file_names or []),
        "scenes_testpoints": [],
        "selected_point_names": [],
        "test_cases": [],
        "retrieval_trace": None,
        "current_phase": "scenes_testpoints",
        "error": None,
    }


def _eval_tracing_note(
    eval_run_id: str,
    dataset_item_id: str,
) -> Dict[str, str]:
    """离线节点不经 RunnableConfig；将 eval 关联信息写入产出供对账 / 后续 Langfuse 对齐。"""
    note = {
        "eval_run_id": eval_run_id,
        "dataset_item_id": dataset_item_id,
        "langfuse_tracing_enabled": str(LangfuseConfig.langfuse_tracing_enabled).lower(),
    }
    return note


def _apply_fixture_scenes(state: Dict[str, Any], item: Dict[str, Any]) -> bool:
    """cases scope 可选预置 scenes，跳过阶段 A LLM。"""
    fixture = item.get("fixture_scenes_testpoints")
    if not isinstance(fixture, list) or not fixture:
        return False
    state["scenes_testpoints"] = fixture
    state["error"] = None
    state["current_phase"] = "testpoints_confirm"
    return True


def run_test_case_item(
    item: Dict[str, Any],
    *,
    dataset_dir,
    eval_run_id: str,
    scope: EvalScope = "full",
) -> Dict[str, Any]:
    """
    执行单条 dataset item。

    scope:
      - testpoints: 仅阶段 A
      - cases: 阶段 B（无 fixture 时先跑阶段 A）
      - full: 阶段 A → 全量采纳测试点 → 阶段 B
    """
    t0 = time.perf_counter()
    document_context = resolve_document_context(item, dataset_dir)
    query = item_scenario_query(item)
    state: Dict[str, Any] = _initial_state(query=query, document_context=document_context)

    tracing = _eval_tracing_note(eval_run_id, item["id"])
    latency_testpoints_ms: Optional[int] = None
    latency_cases_ms: Optional[int] = None

    run_testpoints = scope in ("testpoints", "full", "cases")
    run_cases = scope in ("cases", "full")

    if run_testpoints and scope != "cases":
        t_tp = time.perf_counter()
        cmd1 = generate_scenes_testpoints_node(state)  # type: ignore[arg-type]
        state = _merge_command(state, cmd1)
        latency_testpoints_ms = int((time.perf_counter() - t_tp) * 1000)

        if state.get("error"):
            return _build_result(
                item=item,
                state=state,
                tracing=tracing,
                t0=t0,
                scope=scope,
                latency_testpoints_ms=latency_testpoints_ms,
                latency_cases_ms=None,
            )
    elif run_cases and _apply_fixture_scenes(state, item):
        latency_testpoints_ms = 0
    elif run_cases:
        t_tp = time.perf_counter()
        cmd1 = generate_scenes_testpoints_node(state)  # type: ignore[arg-type]
        state = _merge_command(state, cmd1)
        latency_testpoints_ms = int((time.perf_counter() - t_tp) * 1000)
        if state.get("error"):
            return _build_result(
                item=item,
                state=state,
                tracing=tracing,
                t0=t0,
                scope=scope,
                latency_testpoints_ms=latency_testpoints_ms,
                latency_cases_ms=None,
            )

    if run_cases:
        state["selected_point_names"] = collect_all_point_names(
            state.get("scenes_testpoints") or [],
        )
        state["current_phase"] = "test_cases"
        t_cases = time.perf_counter()
        cmd2 = asyncio.run(generate_test_cases_node(state))  # type: ignore[arg-type]
        state = _merge_command(state, cmd2)
        latency_cases_ms = int((time.perf_counter() - t_cases) * 1000)

    return _build_result(
        item=item,
        state=state,
        tracing=tracing,
        t0=t0,
        scope=scope,
        latency_testpoints_ms=latency_testpoints_ms,
        latency_cases_ms=latency_cases_ms,
    )


def _build_result(
    *,
    item: Dict[str, Any],
    state: Dict[str, Any],
    tracing: Dict[str, str],
    t0: float,
    scope: EvalScope,
    latency_testpoints_ms: Optional[int],
    latency_cases_ms: Optional[int],
) -> Dict[str, Any]:
    return {
        "dataset_item_id": item["id"],
        "scope": scope,
        "tracing": tracing,
        "state": _public_state(state),
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "latency_ms_testpoints": latency_testpoints_ms,
        "latency_ms_cases": latency_cases_ms,
    }


def _public_state(state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "query": state.get("query"),
        "current_phase": state.get("current_phase"),
        "error": state.get("error"),
        "scenes_testpoints": state.get("scenes_testpoints") or [],
        "selected_point_names": state.get("selected_point_names") or [],
        "test_cases": state.get("test_cases") or [],
        "retrieval_trace": state.get("retrieval_trace") or {},
    }
