"""DeepDoc 解析产物 → 入库 Document。"""
from __future__ import annotations

from typing import List, Mapping

from langchain_core.documents import Document

from kb.chunk.params import _normalize_chunk_params, _fixed_window_chunks
from kb.document_parse.deepdoc_result import DeepDocBlock, DeepDocParseResult, DeepDocTable
from kb.document_parse.models import ParsedFile


def _infer_header_path(block: DeepDocBlock) -> str:
    layout = (block.layout_type or "").lower()
    style = str((block.metadata or {}).get("style") or "").lower()
    if layout in {"title", "section-header", "header"} or "heading" in style:
        return block.content.strip()
    return ""


def _flush_chunk(
    parts: List[str],
    *,
    parsed: ParsedFile,
    chunk_index: int,
    page_no,
    layout_type: str,
    header_path: str,
) -> Document | None:
    content = "\n\n".join(p.strip() for p in parts if p and p.strip()).strip()
    if not content:
        return None
    meta = {
        "file_name": parsed.file_name,
        "source_name": parsed.file_name,
        "source": parsed.file_path,
        "file_type": parsed.file_type,
        "update_time": parsed.update_time,
        "chunk_index": chunk_index,
        "raw_text": content,
        "clean_text": content,
        "header_path": header_path,
        "layout_type": layout_type,
    }
    if page_no is not None:
        meta["page_no"] = page_no
    return Document(page_content=content, metadata=meta)


def _chunk_blocks(
    parsed: ParsedFile,
    blocks: List[DeepDocBlock],
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> List[Document]:
    documents: List[Document] = []
    buffer: List[str] = []
    buffer_page = None
    buffer_layout = "text"
    header_path = ""
    size = 0

    def emit() -> None:
        nonlocal buffer, buffer_page, buffer_layout, header_path, size
        doc = _flush_chunk(
            buffer,
            parsed=parsed,
            chunk_index=len(documents),
            page_no=buffer_page,
            layout_type=buffer_layout,
            header_path=header_path,
        )
        if doc:
            documents.append(doc)
        if chunk_overlap > 0 and buffer:
            carry = buffer[-1][-chunk_overlap:]
            buffer = [carry] if carry.strip() else []
            size = len(carry)
        else:
            buffer = []
            size = 0

    for block in blocks:
        text = (block.content or "").strip()
        if not text:
            continue
        inferred = _infer_header_path(block)
        if inferred:
            if buffer:
                emit()
            header_path = inferred
        layout = (block.layout_type or "text").lower()
        if layout in {"table_row", "slide"}:
            if buffer:
                emit()
            doc = _flush_chunk(
                [text],
                parsed=parsed,
                chunk_index=len(documents),
                page_no=block.page_no,
                layout_type=layout,
                header_path=header_path,
            )
            if doc:
                documents.append(doc)
            continue
        if layout in {"table", "figure", "figure caption", "image"} or size + len(text) > chunk_size:
            if buffer:
                emit()
        buffer.append(text)
        size += len(text)
        buffer_page = block.page_no or buffer_page
        buffer_layout = layout
        if size >= chunk_size:
            emit()
    if buffer:
        emit()
    return documents


def _chunk_tables(parsed: ParsedFile, tables: List[DeepDocTable], *, chunk_size: int) -> List[Document]:
    documents: List[Document] = []
    for table in tables:
        text = (table.content or "").strip()
        if not text:
            continue
        if len(text) <= chunk_size:
            parts = [text]
        else:
            parts = _fixed_window_chunks(text, chunk_size=chunk_size, overlap=0)
        for part in parts:
            doc = _flush_chunk(
                [part],
                parsed=parsed,
                chunk_index=len(documents),
                page_no=table.page_no,
                layout_type="table",
                header_path="",
            )
            if doc:
                documents.append(doc)
    return documents


def adapt_deepdoc_to_documents(
    parsed: ParsedFile,
    *,
    effective_params: Mapping[str, object],
) -> List[Document]:
    result: DeepDocParseResult | None = parsed.deepdoc_result
    if result is None:
        return []

    if parsed.is_tabular and parsed.row_documents:
        finalized: List[Document] = []
        for idx, doc in enumerate(parsed.row_documents):
            meta = dict(doc.metadata or {})
            meta["chunk_index"] = idx
            meta["domain"] = parsed.domain
            meta["business"] = parsed.business
            finalized.append(Document(page_content=doc.page_content, metadata=meta))
        return finalized

    chunk_size, chunk_overlap = _normalize_chunk_params(effective_params)
    blocks = list(result.blocks) + list(result.figures)
    documents = _chunk_blocks(parsed, blocks, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    documents.extend(_chunk_tables(parsed, result.tables, chunk_size=chunk_size))
    for idx, doc in enumerate(documents):
        doc.metadata["chunk_index"] = idx
        doc.metadata["domain"] = parsed.domain
        doc.metadata["business"] = parsed.business
    return documents
