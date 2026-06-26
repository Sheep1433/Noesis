"""promptfoo RAG 评测 provider：场景级两路检索 + 当前需求全文注入。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parents[3]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agent.case_generate.case_graph import _build_scene_cases_prompt
from agent.case_generate.rag import build_scene_rag_context
from config.env import QdrantConfig
from evals.case.rag.ingest import eval_requirement_collection, eval_test_case_collection
from evals.case.shared.provider_common import eval_run_id, resolve_document_context, tracing_note
from evals.langfuse_env import eval_langfuse_run


_EVAL_DIR = Path(__file__).resolve().parent


def _resolve_item(context: Dict[str, Any]) -> Dict[str, Any]:
    vars_ = context.get("vars") or {}
    item_id = vars_.get("item_id")
    if not item_id:
        raise ValueError("测试用例缺少 vars.item_id")
    rag_scene = vars_.get("rag_scene")
    if not isinstance(rag_scene, dict):
        raise ValueError(f"测试用例 {item_id} 缺少 vars.rag_scene")
    document_path = vars_.get("document_path")
    if not document_path:
        raise ValueError(f"测试用例 {item_id} 缺少 vars.document_path")
    return {
        "id": item_id,
        "document_path": document_path,
        "rag_scene": rag_scene,
    }


def _ensure_qdrant() -> None:
    from services.qdrant_service import init_qdrant_client

    if not asyncio.run(init_qdrant_client()):
        raise RuntimeError(
            "Qdrant 连接失败：RAG 评测需要向量库。"
            "请确认 Qdrant 已启动；可先运行 uv run python -m evals.case.rag.ingest"
        )


async def _run_rag_async(item: Dict[str, Any], *, eval_run_id: str) -> Dict[str, Any]:
    t0 = time.perf_counter()
    tracing = tracing_note(eval_run_id, item["id"])
    document_context = resolve_document_context(item, base_dir=_EVAL_DIR)
    scene_cfg = dict(item["rag_scene"])
    scene = {
        "scene_name": scene_cfg.get("scene_name", ""),
        "scene_description": scene_cfg.get("scene_description", ""),
    }
    adopted = [str(x).strip() for x in (scene_cfg.get("adopted_point_names") or []) if str(x).strip()]
    source_names = [str(x).strip() for x in (scene_cfg.get("source_file_names") or []) if str(x).strip()]

    eval_qdrant = replace(
        QdrantConfig,
        case_rag_historical_requirements_enabled=True,
        requirement_docs_collection=eval_requirement_collection(),
        test_case_docs_collection=eval_test_case_collection(),
        test_case_upload_collection="",
    )
    with patch("agent.case_generate.rag.QdrantConfig", eval_qdrant):
        scene_rag_context, trace_entry = await build_scene_rag_context(
            scene,
            adopted_point_names=adopted,
            source_file_names=source_names,
        )

    scene_name = str(scene.get("scene_name") or "").strip()
    points = [
        {"point_name": n, "point_level": "P1", "point_type": "functional"}
        for n in adopted
    ]
    assembled_prompt = _build_scene_cases_prompt(
        scene_name,
        points,
        scene_rag_context,
        document_context=document_context,
    )
    doc_injected = bool(document_context.strip()) and "## 当前需求文档" in assembled_prompt

    return {
        "dataset_item_id": item["id"],
        "tracing": tracing,
        "scene_name": scene_name,
        "retrieval_trace": {scene_name: trace_entry},
        "scene_rag_context": scene_rag_context,
        "document_context_chars": len(document_context),
        "document_context_injected": doc_injected,
        "assembled_prompt_chars": len(assembled_prompt),
        "latency_ms": int((time.perf_counter() - t0) * 1000),
    }


def run_rag(item: Dict[str, Any], *, eval_run_id: str) -> Dict[str, Any]:
    return asyncio.run(_run_rag_async(item, eval_run_id=eval_run_id))


def call_api(
    prompt: str,
    options: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
):
    context = context or {}
    item = _resolve_item(context)
    _ensure_qdrant()

    tag = os.environ.get("NOESIS_CASE_EVAL_TAG") or os.environ.get("NOESIS_EVAL_TAG") or "promptfoo"
    run_id = eval_run_id(tag)
    session_id = f"eval-case-rag-{item['id']}-{run_id}"

    with eval_langfuse_run(line="case", tag=tag, session_id=session_id):
        run_output = run_rag(item, eval_run_id=run_id)

    return {
        "output": json.dumps(run_output, ensure_ascii=False),
        "metadata": {
            "dataset_item_id": item["id"],
            "eval_run_id": run_id,
            "latency_ms": run_output.get("latency_ms"),
        },
    }
