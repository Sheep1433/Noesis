"""Qdrant point payload 构建，与 VectorStore.add_vectors 顶层字段对齐。"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional

from langchain_core.documents import Document
from qdrant_client.models import PointStruct

_IMAGE_RAW_TEXT_MAX_LEN = 4096


def compute_content_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def hash_to_uuid(content_hash: str) -> str:
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_DNS, content_hash))


def build_payload(
    *,
    page_content: str,
    metadata: Dict[str, Any],
    chunk_index: int = 0,
    file_hash: Optional[str] = None,
    effective_processing_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构建与 VectorStore 一致的顶层 payload。"""
    now = datetime.now().isoformat()
    element_type = metadata.get("element_type", "text")
    raw_text = metadata.get("raw_text", page_content)
    if element_type == "image" and len(raw_text) > _IMAGE_RAW_TEXT_MAX_LEN:
        raw_text = raw_text[:_IMAGE_RAW_TEXT_MAX_LEN] + "...[truncated]"

    content_hash = metadata.get("content_hash") or compute_content_hash(page_content)
    meta = dict(metadata)
    meta["content_hash"] = content_hash

    payload: Dict[str, Any] = {
        "page_content": page_content,
        "content": page_content,
        "content_hash": content_hash,
        "file_name": metadata.get("file_name") or metadata.get("source_name", ""),
        "source": metadata.get("source", ""),
        "chunk_index": metadata.get("chunk_index", chunk_index),
        "file_type": metadata.get("file_type", ""),
        "raw_text": raw_text,
        "clean_text": metadata.get("clean_text", page_content),
        "created_at": metadata.get("created_at", now),
        "update_time": metadata.get("update_time", now),
        "element_type": element_type,
        "domain": metadata.get("domain", ""),
        "business": metadata.get("business", ""),
        "header_path": metadata.get("header_path", ""),
        "source_name": metadata.get("source_name") or metadata.get("file_name", ""),
        "Header_1": metadata.get("Header_1", ""),
        "Header_2": metadata.get("Header_2", ""),
        "Header_3": metadata.get("Header_3", ""),
        "Header_4": metadata.get("Header_4", ""),
        "metadata": meta,
    }
    if file_hash:
        payload["file_hash"] = file_hash
    if effective_processing_params is not None:
        payload["effective_processing_params"] = effective_processing_params
    return payload


def documents_to_points(
    documents: List[Document],
    embeddings: List[List[float]],
    *,
    file_hash: Optional[str] = None,
    effective_processing_params: Optional[Dict[str, Any]] = None,
) -> List[PointStruct]:
    points: List[PointStruct] = []
    for i, (doc, vector) in enumerate(zip(documents, embeddings)):
        text = (doc.page_content or "").strip()
        if not text:
            continue
        meta = dict(doc.metadata or {})
        payload = build_payload(
            page_content=text,
            metadata=meta,
            chunk_index=int(meta.get("chunk_index", i)),
            file_hash=file_hash,
            effective_processing_params=effective_processing_params,
        )
        point_id = hash_to_uuid(payload["content_hash"])
        points.append(PointStruct(id=point_id, vector=vector, payload=payload))
    return points
