"""测试用例 Agent 离线 runner（直调 case_graph：场景测试点 → 测试用例）。"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from langgraph.types import Command

from agent.case_generate.case_graph import (
    TestCaseState,
    generate_scenes_testpoints_node,
    generate_test_cases_node,
)
from config.env import LangfuseConfig
from evals.langfuse_env import load_eval_langfuse_settings
from evals.case.dataset import resolve_document_context


def item_scenario_query(item: Dict[str, Any]) -> str:
    for key in ("scenario_description", "query"):
        val = item.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def collect_all_point_names(scenes_testpoints: List[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    seen: set[str] = set()
    for scene in scenes_testpoints or []:
        for tp in scene.get("test_points") or []:
            n = str(tp.get("point_name") or "").strip()
            if n and n not in seen:
                seen.add(n)
                names.append(n)
    return names


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


def _eval_tracing_note(eval_run_id: str, dataset_item_id: str) -> Dict[str, str]:
    eval_lf = load_eval_langfuse_settings()
    return {
        "eval_run_id": eval_run_id,
        "dataset_item_id": dataset_item_id,
        "langfuse_tracing_enabled": str(
            eval_lf.tracing_enabled if eval_lf else LangfuseConfig.langfuse_tracing_enabled
        ).lower(),
        "langfuse_source": "evals/.env" if eval_lf else "app",
    }


def run_test_case_item(
    item: Dict[str, Any],
    *,
    dataset_dir,
    eval_run_id: str,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    document_context = resolve_document_context(item, dataset_dir)
    query = item_scenario_query(item)
    state: Dict[str, Any] = _initial_state(query=query, document_context=document_context)
    tracing = _eval_tracing_note(eval_run_id, item["id"])

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
            latency_testpoints_ms=latency_testpoints_ms,
            latency_cases_ms=None,
        )

    state["selected_point_names"] = collect_all_point_names(state.get("scenes_testpoints") or [])
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
        latency_testpoints_ms=latency_testpoints_ms,
        latency_cases_ms=latency_cases_ms,
    )


def _build_result(
    *,
    item: Dict[str, Any],
    state: Dict[str, Any],
    tracing: Dict[str, str],
    t0: float,
    latency_testpoints_ms: Optional[int],
    latency_cases_ms: Optional[int],
) -> Dict[str, Any]:
    return {
        "dataset_item_id": item["id"],
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
