"""promptfoo 共用：文档解析与 Langfuse 元数据。"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from config.env import LangfuseConfig
from evals.langfuse_env import load_eval_langfuse_settings


def resolve_document_context(item: Dict[str, Any], *, base_dir: Path) -> str:
    if item.get("document_context"):
        return str(item["document_context"])
    doc_path = item.get("document_path")
    if not doc_path:
        return ""
    rel = Path(str(doc_path))
    full = rel if rel.is_absolute() else (base_dir / rel).resolve()
    if not full.is_file():
        raise FileNotFoundError(f"文档不存在: {full}")
    return full.read_text(encoding="utf-8")


def eval_run_id(tag: str) -> str:
    custom = os.environ.get("NOESIS_CASE_EVAL_RUN_ID") or os.environ.get("NOESIS_EVAL_RUN_ID")
    if custom:
        return custom
    return f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{tag}_{uuid.uuid4().hex[:8]}"


def tracing_note(eval_run_id: str, dataset_item_id: str) -> Dict[str, str]:
    eval_lf = load_eval_langfuse_settings()
    return {
        "eval_run_id": eval_run_id,
        "dataset_item_id": dataset_item_id,
        "langfuse_tracing_enabled": str(
            eval_lf.tracing_enabled if eval_lf else LangfuseConfig.langfuse_tracing_enabled
        ).lower(),
        "langfuse_source": "evals/.env" if eval_lf else "app",
    }
