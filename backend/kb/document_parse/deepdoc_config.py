"""DeepDoc 配置：模型目录与 RAGFlow 路径约定。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

DEEPDOC_UPSTREAM_COMMIT = "828c5789f651d4c4ebe4645190b8b8d244144fe0"
RAGFLOW_DEEPDOC_REL = Path("rag") / "res" / "deepdoc"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_path(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = (_repo_root() / path).resolve()
    return path


@lru_cache
def get_deepdoc_model_dir() -> Path:
    from config.env import KbConfig

    return _resolve_path(KbConfig.deepdoc_model_dir)


def resolve_rag_project_base() -> str:
    """使 `join(base, rag/res/deepdoc)` 指向配置的 model_dir。"""
    model_dir = get_deepdoc_model_dir()
    rel_parts = RAGFLOW_DEEPDOC_REL.parts
    base = model_dir
    for _ in rel_parts:
        base = base.parent
    return str(base)


def ensure_model_dir() -> Path:
    model_dir = get_deepdoc_model_dir()
    model_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("RAG_PROJECT_BASE", resolve_rag_project_base())
    return model_dir


def list_required_model_files() -> tuple[str, ...]:
    return (
        "layout.onnx",
        "det.onnx",
        "rec.onnx",
        "tsr.onnx",
        "ocr.res",
        "updown_concat_xgb.model",
    )


def models_available() -> bool:
    model_dir = get_deepdoc_model_dir()
    return all((model_dir / name).is_file() for name in list_required_model_files())
