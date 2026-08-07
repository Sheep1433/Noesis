"""Qdrant point payload 构建，与 VectorStore.add_vectors 顶层字段对齐。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from langchain_core.documents import Document
from qdrant_client.models import PointStruct

_IMAGE_RAW_TEXT_MAX_LEN = 4096


def _stable_public_id(prefix: str, *parts: object) -> str:
    canonical = json.dumps(
        [str(part or "").strip() for part in parts],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:32]}"


def build_evidence_identity(
    *,
    collection_name: str,
    file_name: str,
    file_hash: str,
    chunk_index: int,
    content_hash: str,
) -> Dict[str, str]:
    """生成公开且可重放的 document/version/segment 三层身份。"""
    document_id = _stable_public_id("doc", collection_name, file_name)
    document_version_id = _stable_public_id("docv", document_id, file_hash)
    segment_id = _stable_public_id(
        "seg", document_version_id, chunk_index, content_hash
    )
    return {
        "document_id": document_id,
        "document_version_id": document_version_id,
        "segment_id": segment_id,
    }


def build_typed_locator(metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    locator = metadata.get("locator")
    if isinstance(locator, dict) and locator.get("type") in {
        "page",
        "char",
        "bbox",
        "header",
    }:
        return dict(locator)
    page_no = metadata.get("page_no") or metadata.get("page_number")
    if isinstance(page_no, int) and page_no > 0:
        return {"type": "page", "page_start": page_no, "page_end": page_no}
    header_path = str(metadata.get("header_path") or "").strip()
    if header_path:
        return {
            "type": "header",
            "path": [part.strip() for part in header_path.split(">") if part.strip()],
        }
    return None


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
    collection_name: str = "",
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
    resolved_chunk_index = int(metadata.get("chunk_index", chunk_index))
    resolved_file_name = str(
        metadata.get("file_name") or metadata.get("source_name", "")
    )
    meta = dict(metadata)
    meta["content_hash"] = content_hash

    identity: Dict[str, str] = {}
    if collection_name and resolved_file_name and file_hash:
        identity = build_evidence_identity(
            collection_name=collection_name,
            file_name=resolved_file_name,
            file_hash=file_hash,
            chunk_index=resolved_chunk_index,
            content_hash=content_hash,
        )
        meta.update(identity)
    locator = build_typed_locator(meta)
    if locator is not None:
        meta["locator"] = locator

    payload: Dict[str, Any] = {
        "page_content": page_content,
        "content": page_content,
        "content_hash": content_hash,
        "file_name": resolved_file_name,
        "source": metadata.get("source", ""),
        "chunk_index": resolved_chunk_index,
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
    payload.update(identity)
    if locator is not None:
        payload["locator"] = locator
    if effective_processing_params is not None:
        payload["effective_processing_params"] = effective_processing_params
    return payload


def documents_to_points(
    documents: List[Document],
    embeddings: List[List[float]],
    *,
    collection_name: str = "",
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
            collection_name=collection_name,
            file_hash=file_hash,
            effective_processing_params=effective_processing_params,
        )
        point_id = hash_to_uuid(payload["content_hash"])
        points.append(PointStruct(id=point_id, vector=vector, payload=payload))
    return points


def payload_created_at(payload: Dict[str, Any]) -> Optional[str]:
    """分片入库时间：读取 created_at，兼容历史数据的 update_time。"""
    value = payload.get("created_at") or payload.get("update_time")
    return str(value) if value else None


def vector_length(vector: Any) -> int:
    if not vector:
        return 0
    if isinstance(vector, dict):
        first = next(iter(vector.values()), None)
        return len(first) if first else 0
    return len(vector)
