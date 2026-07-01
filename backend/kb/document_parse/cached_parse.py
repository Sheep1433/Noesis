"""带 JSON 缓存的解析入口。"""
from __future__ import annotations

from typing import Optional

from kb.document_parse.factory import ParserFactory
from kb.document_parse.models import ParsedFile
from kb.document_parse.parse_cache import load_parse_cache, save_parse_cache
from kb.document_parse.deepdoc_service import parse_file_with_deepdoc
from kb.document_parse.source_name import apply_source_file_name


def parse_file_cached(
    file_path: str,
    *,
    collection_name: str,
    file_hash: str,
    source_file_name: Optional[str] = None,
    domain: Optional[str] = None,
    business: Optional[str] = None,
    parser_id: Optional[str] = None,
    use_cache: bool = True,
) -> ParsedFile:
    effective = (parser_id or "deepdoc").strip().lower()
    if effective != "deepdoc":
        raise ValueError(f"仅支持 parser_id=deepdoc，收到: {effective}")

    if use_cache and file_hash:
        cached = load_parse_cache(collection_name, file_hash)
        if cached is not None:
            if source_file_name:
                cached.source_file_name = source_file_name.strip()
            cached.file_path = file_path
            parsed = ParserFactory.from_deepdoc_result(cached)
            return apply_source_file_name(parsed, source_file_name or parsed.file_name)

    result = parse_file_with_deepdoc(
        file_path,
        source_file_name=source_file_name,
        domain=domain,
        business=business,
    )
    if file_hash:
        save_parse_cache(collection_name, file_hash, result)
    parsed = ParserFactory.from_deepdoc_result(result)
    return apply_source_file_name(parsed, source_file_name or parsed.file_name)
