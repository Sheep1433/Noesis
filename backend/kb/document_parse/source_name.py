"""解析产物上的原始文件名修正。"""
from __future__ import annotations

from kb.document_parse.models import ParsedFile


def apply_source_file_name(parsed: ParsedFile, source_file_name: str) -> ParsedFile:
    name = (source_file_name or "").strip()
    if not name or parsed.file_name == name:
        return parsed

    parsed.file_name = name
    if parsed.deepdoc_result is not None:
        parsed.deepdoc_result.source_file_name = name
    if parsed.row_documents:
        for doc in parsed.row_documents:
            doc.metadata["file_name"] = name
            if "source_name" in doc.metadata:
                doc.metadata["source_name"] = name
    return parsed
