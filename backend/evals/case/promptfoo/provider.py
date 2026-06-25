"""promptfoo provider：调用测试用例 Agent 离线 runner。"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[3]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from evals.case.fixtures import resolve_document_context
from evals.case.runner import run_test_case_item
from evals.langfuse_env import eval_langfuse_run


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


def _eval_run_id(tag: str) -> str:
    custom = os.environ.get("NOESIS_CASE_EVAL_RUN_ID") or os.environ.get("NOESIS_EVAL_RUN_ID")
    if custom:
        return custom
    return f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{tag}_{uuid.uuid4().hex[:8]}"


def _ensure_qdrant() -> None:
    from services.qdrant_service import init_qdrant_client

    if not asyncio.run(init_qdrant_client()):
        raise RuntimeError(
            "Qdrant 连接失败：测试用例生成需要向量库。"
            "请确认 Qdrant 已启动且 .env 中 qdrant_host/qdrant_port 正确。"
        )


def call_api(prompt: str, options: Optional[Dict[str, Any]] = None, context: Optional[Dict[str, Any]] = None):
    context = context or {}
    item = _resolve_item(context)
    _ensure_qdrant()

    tag = (
        os.environ.get("NOESIS_CASE_EVAL_TAG")
        or os.environ.get("NOESIS_EVAL_TAG")
        or "promptfoo"
    )
    eval_run_id = _eval_run_id(tag)

    session_id = f"eval-case-{item.get('id')}-{eval_run_id}"
    with eval_langfuse_run(line="case", tag=tag, session_id=session_id):
        run_output = run_test_case_item(item, eval_run_id=eval_run_id)

    return {
        "output": json.dumps(run_output, ensure_ascii=False),
        "metadata": {
            "dataset_item_id": item.get("id"),
            "eval_run_id": eval_run_id,
            "latency_ms": run_output.get("latency_ms"),
            "document_context_chars": len(resolve_document_context(item)),
        },
    }
