"""promptfoo 测试点评测 provider：仅生成场景与测试点。"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from langgraph.types import Command

_BACKEND = Path(__file__).resolve().parents[3]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agent.case_generate.case_graph import TestCaseState, generate_scenes_testpoints_node
from evals.case.shared.provider_common import eval_run_id, resolve_document_context, tracing_note
from evals.langfuse_env import eval_langfuse_run


def _merge_command(state: Dict[str, Any], command: Command) -> Dict[str, Any]:
    out = dict(state)
    out.update(getattr(command, "update", None) or {})
    return out


def _initial_state(query: str, document_context: str) -> TestCaseState:
    return {
        "query": query,
        "document_context": document_context,
        "source_file_names": [],
        "scenes_testpoints": [],
        "selected_point_names": [],
        "test_cases": [],
        "retrieval_trace": None,
        "current_phase": "scenes_testpoints",
        "error": None,
    }


def _resolve_item(context: Dict[str, Any]) -> Dict[str, Any]:
    vars_ = context.get("vars") or {}
    item_id = vars_.get("item_id")
    if not item_id:
        raise ValueError("测试用例缺少 vars.item_id")
    document_path = vars_.get("document_path")
    if not document_path:
        raise ValueError(f"测试用例 {item_id} 缺少 vars.document_path")
    return {
        "id": item_id,
        "scenario_description": vars_.get("scenario_description") or "",
        "document_path": document_path,
        "ground_truth": vars_.get("ground_truth") or {},
    }


_EVAL_DIR = Path(__file__).resolve().parent


def run_testpoints(item: Dict[str, Any], *, eval_run_id: str) -> Dict[str, Any]:
    t0 = time.perf_counter()
    query = str(item.get("scenario_description") or "").strip()
    document_context = resolve_document_context(item, base_dir=_EVAL_DIR)
    state: Dict[str, Any] = _initial_state(query=query, document_context=document_context)
    tracing = tracing_note(eval_run_id, item["id"])

    t_tp = time.perf_counter()
    state = _merge_command(state, generate_scenes_testpoints_node(state))  # type: ignore[arg-type]
    latency_testpoints_ms = int((time.perf_counter() - t_tp) * 1000)

    return {
        "dataset_item_id": item["id"],
        "tracing": tracing,
        "state": {
            "query": state.get("query"),
            "error": state.get("error"),
            "scenes_testpoints": state.get("scenes_testpoints") or [],
        },
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "latency_ms_testpoints": latency_testpoints_ms,
    }


def call_api(
    prompt: str,
    options: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
):
    context = context or {}
    item = _resolve_item(context)
    tag = os.environ.get("NOESIS_CASE_EVAL_TAG") or os.environ.get("NOESIS_EVAL_TAG") or "promptfoo"
    run_id = eval_run_id(tag)
    session_id = f"eval-case-tp-{item['id']}-{run_id}"

    with eval_langfuse_run(line="case", tag=tag, session_id=session_id):
        run_output = run_testpoints(item, eval_run_id=run_id)

    return {
        "output": json.dumps(run_output, ensure_ascii=False),
        "metadata": {
            "dataset_item_id": item["id"],
            "eval_run_id": run_id,
            "latency_ms": run_output.get("latency_ms"),
            "document_context_chars": len(resolve_document_context(item, base_dir=_EVAL_DIR)),
        },
    }
