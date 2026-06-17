"""分块实现：ParsedFile 或 Markdown 文本 → 入库 Document。"""
from __future__ import annotations

from typing import List, Mapping, Optional, Union

from langchain_core.documents import Document

from kb.chunk.markdown_splitter import MarkdownChunker
from kb.chunk.params import _normalize_chunk_params, _fixed_window_chunks
from kb.document_parse.models import ParsedFile
from common.logging import logger

ChunkInput = Union[ParsedFile, str]


def _finalize_documents(parsed: ParsedFile, documents: List[Document]) -> List[Document]:
    finalized: List[Document] = []
    for idx, doc in enumerate(documents):
        content = (doc.metadata.get("clean_text") or doc.page_content or "").strip()
        if not content:
            continue
        meta = dict(doc.metadata or {})
        meta.setdefault("file_name", parsed.file_name)
        meta.setdefault("source_name", parsed.file_name)
        meta.setdefault("source", parsed.file_path)
        meta.setdefault("chunk_index", idx)
        meta.setdefault("raw_text", doc.page_content)
        meta.setdefault("clean_text", content)
        meta["domain"] = parsed.domain
        meta["business"] = parsed.business
        finalized.append(Document(page_content=content, metadata=meta))
    return finalized


def _chunk_parsed_file(
    parsed: ParsedFile,
    *,
    effective_params: Mapping[str, object],
) -> List[Document]:
    if parsed.is_tabular:
        return _finalize_documents(parsed, list(parsed.row_documents or []))

    text = (parsed.clean_markdown or parsed.raw_markdown or "").strip()
    if not text:
        return []

    chunk_size, chunk_overlap = _normalize_chunk_params(effective_params)
    docs = MarkdownChunker.split_markdown_with_headers(
        raw_markdown=parsed.raw_markdown or text,
        clean_markdown=parsed.clean_markdown or text,
        source=parsed.file_path,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    for doc in docs:
        doc.metadata.setdefault("file_name", parsed.file_name)
        doc.metadata.setdefault("file_type", parsed.file_type)
        doc.metadata.setdefault("update_time", parsed.update_time)
    return _finalize_documents(parsed, docs)


def _chunk_markdown_text(
    text: str,
    *,
    effective_params: Mapping[str, object],
    source_hint: str = "",
    source_path: Optional[str] = None,
) -> List[Document]:
    if not (text or "").strip():
        return []

    chunk_size, chunk_overlap = _normalize_chunk_params(effective_params)
    src = source_path or source_hint or "inline.md"
    file_type = ""
    if source_path and "." in source_path:
        file_type = source_path.rsplit(".", 1)[-1].lower()

    try:
        docs = MarkdownChunker.split_markdown_with_headers(
            raw_markdown=text,
            clean_markdown=text,
            source=src,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        out: List[Document] = []
        for i, doc in enumerate(docs):
            content = (doc.page_content or "").strip()
            if not content:
                continue
            meta = dict(doc.metadata or {})
            meta.setdefault("file_name", source_hint)
            meta.setdefault("source_name", source_hint)
            meta.setdefault("source", source_path or source_hint)
            meta.setdefault("file_type", file_type)
            meta.setdefault("chunk_index", i)
            out.append(Document(page_content=content, metadata=meta))
        if out:
            return out
    except Exception as exc:
        logger.warning(f"[kb.chunk] Markdown 标题分块失败，回退滑窗: {exc}")

    parts = _fixed_window_chunks(text, chunk_size=chunk_size, overlap=chunk_overlap)
    return [
        Document(
            page_content=part,
            metadata={
                "file_name": source_hint,
                "source_name": source_hint,
                "source": source_path or source_hint,
                "file_type": file_type,
                "chunk_index": i,
                "header_path": "",
                "Header_1": "",
                "Header_2": "",
                "Header_3": "",
                "Header_4": "",
            },
        )
        for i, part in enumerate(parts)
    ]


def chunk(
    content: ChunkInput,
    *,
    effective_params: Mapping[str, object],
    source_hint: str = "",
    source_path: Optional[str] = None,
) -> List[Document]:
    """
    统一分块入口。

    - ParsedFile：parse 层输出（含表格行、raw/clean Markdown 对）
    - str：已有 Markdown 文本（跳过 parse，如内存片段）
    """
    if isinstance(content, ParsedFile):
        return _chunk_parsed_file(content, effective_params=effective_params)
    return _chunk_markdown_text(
        content,
        effective_params=effective_params,
        source_hint=source_hint,
        source_path=source_path,
    )
