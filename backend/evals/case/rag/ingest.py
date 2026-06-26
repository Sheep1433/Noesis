"""RAG 评测灌库：分块、写 id_map.json、可选 upsert 到隔离 Qdrant collection。

历史需求语料：testpoints/documents/
历史用例语料：rag/corpus/test_cases/

用法：
  cd backend
  uv run python -m evals.case.rag.ingest --map-only
  uv run python -m evals.case.rag.ingest --reset
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.documents import Document
from qdrant_client.models import PointStruct

from common.logging import logger
from kb.chunk import chunk
from kb.chunk.params import fixed_processing_params
from kb.document_parse.parser import DocumentParser
from kb.embedding import embedding_not_configured_message, get_embedding, is_embedding_configured
from kb.retrieval import KbRetrievalService
from kb.retrieval.payload import compute_content_hash, documents_to_points, hash_to_uuid

RAG_DIR = Path(__file__).resolve().parent
CASE_ROOT = RAG_DIR.parent
REQUIREMENTS_DIR = CASE_ROOT / "testpoints" / "documents"
TEST_CASES_DIR = RAG_DIR / "corpus" / "test_cases"
ID_MAP_PATH = RAG_DIR / "id_map.json"

FIXTURE_VERSION = "2026-06-26-v1"
DEFAULT_REQ_COLLECTION = "requirement_docs_eval"
DEFAULT_TC_COLLECTION = "test_case_docs_eval"
EVAL_VECTOR_DIM = 1024


def eval_requirement_collection() -> str:
    return (
        os.environ.get("NOESIS_CASE_EVAL_REQ_COLLECTION", DEFAULT_REQ_COLLECTION).strip()
        or DEFAULT_REQ_COLLECTION
    )


def eval_test_case_collection() -> str:
    return (
        os.environ.get("NOESIS_CASE_EVAL_TC_COLLECTION", DEFAULT_TC_COLLECTION).strip()
        or DEFAULT_TC_COLLECTION
    )


def _list_md(directory: Path) -> List[Path]:
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.md"))


def file_content_hash(file_path: Path) -> str:
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def chunk_markdown_file(file_path: Path, *, file_name: Optional[str] = None) -> List[Document]:
    name = file_name or file_path.name
    ef_params = fixed_processing_params()
    try:
        parsed = DocumentParser.parse_file(str(file_path))
        documents = chunk(parsed, effective_params=ef_params)
    except ValueError:
        text = file_path.read_text(encoding="utf-8")
        documents = chunk(
            text,
            effective_params=ef_params,
            source_hint=name,
            source_path=str(file_path),
        )
    for doc in documents:
        doc.metadata.setdefault("file_name", name)
        doc.metadata.setdefault("source_name", name)
        doc.metadata.setdefault("source", str(file_path))
    return documents


def documents_to_id_entries(documents: List[Document]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for doc in documents:
        text = (doc.page_content or "").strip()
        if not text:
            continue
        meta = dict(doc.metadata or {})
        content_hash = meta.get("content_hash") or compute_content_hash(text)
        point_id = hash_to_uuid(content_hash)
        preview = text.replace("\n", " ")[:120]
        entries.append(
            {
                "file_name": meta.get("file_name") or meta.get("source_name") or "",
                "chunk_index": int(meta.get("chunk_index", 0)),
                "content_hash": content_hash,
                "point_id": point_id,
                "header_path": meta.get("header_path") or "",
                "content_preview": preview,
            }
        )
    return entries


def build_points_for_upload(documents: List[Document], *, file_hash: str) -> List[PointStruct]:
    if not is_embedding_configured():
        raise RuntimeError(embedding_not_configured_message())

    kept_docs = [d for d in documents if (d.page_content or "").strip()]
    if not kept_docs:
        return []

    embedding = get_embedding()
    embeddings = embedding.embed_documents([(d.page_content or "").strip() for d in kept_docs])
    if not embeddings:
        raise RuntimeError("embedding 返回为空")

    points = documents_to_points(
        kept_docs,
        embeddings,
        file_hash=file_hash,
        effective_processing_params=fixed_processing_params(),
    )
    emb_len = len(embeddings[0]) if embeddings[0] else 0
    if emb_len and emb_len != EVAL_VECTOR_DIM:
        raise RuntimeError(f"嵌入维度 {emb_len} 与 eval collection 维度 {EVAL_VECTOR_DIM} 不一致")
    return points or []


def upsert_points(collection_name: str, points: List[PointStruct]) -> int:
    from services.qdrant_service import QdrantService

    service = QdrantService()
    if not service.client:
        raise RuntimeError("Qdrant 客户端未连接")
    service.client.upsert(collection_name=collection_name, points=points)
    KbRetrievalService.invalidate_cache(collection_name)
    return len(points)


def ingest_markdown_file(
    file_path: Path,
    collection_name: str,
    *,
    file_name: Optional[str] = None,
    upload: bool = True,
) -> List[Dict[str, Any]]:
    documents = chunk_markdown_file(file_path, file_name=file_name)
    entries = documents_to_id_entries(documents)
    if upload and entries:
        points = build_points_for_upload(documents, file_hash=file_content_hash(file_path))
        upsert_points(collection_name, points)
    return entries


def pick_relevant_by_keywords(
    entries: List[Dict[str, Any]],
    *,
    file_name: str,
    keywords: List[str],
    limit: int = 3,
) -> List[str]:
    hits: List[str] = []
    for ent in entries:
        if ent.get("file_name") != file_name:
            continue
        preview = str(ent.get("content_preview") or "")
        if any(kw in preview for kw in keywords):
            hits.append(str(ent["point_id"]))
        if len(hits) >= limit:
            break
    return hits


def _ensure_eval_collections(*, reset: bool) -> None:
    from services.qdrant_service import QdrantService, init_qdrant_client

    if not asyncio.run(init_qdrant_client()):
        raise RuntimeError("Qdrant 连接失败，无法入库 eval 文档")

    service = QdrantService()
    if not service.client:
        raise RuntimeError("Qdrant 客户端不可用")

    for coll in (eval_requirement_collection(), eval_test_case_collection()):
        if reset and service.client.collection_exists(coll):
            service.client.delete_collection(coll)
            logger.info(f"[eval.ingest] 已删除 collection: {coll}")
        result = service.create_collection(coll, vector_dimension=EVAL_VECTOR_DIM)
        if not result.get("success") and result.get("code") != 409:
            raise RuntimeError(f"创建 collection {coll} 失败: {result.get('message')}")


def build_id_map(*, upload: bool, reset: bool) -> Dict[str, Any]:
    req_entries: List[Dict[str, Any]] = []
    tc_entries: List[Dict[str, Any]] = []
    req_coll = eval_requirement_collection()
    tc_coll = eval_test_case_collection()

    if upload:
        _ensure_eval_collections(reset=reset)

    for path in _list_md(REQUIREMENTS_DIR):
        req_entries.extend(ingest_markdown_file(path, req_coll, upload=upload))

    for path in _list_md(TEST_CASES_DIR):
        tc_entries.extend(ingest_markdown_file(path, tc_coll, upload=upload))

    return {
        "fixture_version": FIXTURE_VERSION,
        "requirement_collection": req_coll,
        "test_case_collection": tc_coll,
        "requirements": req_entries,
        "test_cases": tc_entries,
    }


def write_id_map(data: Dict[str, Any]) -> Path:
    ID_MAP_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return ID_MAP_PATH


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="灌入 RAG 评测 Qdrant 文档")
    parser.add_argument("--map-only", action="store_true", help="仅分块写 id_map，不连 Qdrant")
    parser.add_argument("--reset", action="store_true", help="入库前重建 eval collection")
    args = parser.parse_args(argv)

    if not _list_md(REQUIREMENTS_DIR) and not _list_md(TEST_CASES_DIR):
        print("未找到 testpoints/documents 或 rag/corpus/test_cases 下的 .md", file=sys.stderr)
        return 1

    try:
        id_map = build_id_map(upload=not args.map_only, reset=args.reset)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    out = write_id_map(id_map)
    mode = "map-only" if args.map_only else "upload"
    print(
        f"[{mode}] requirements={len(id_map['requirements'])} "
        f"test_cases={len(id_map['test_cases'])} → {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
