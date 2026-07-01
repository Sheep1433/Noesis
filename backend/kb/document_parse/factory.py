"""知识库解析调度：仅 deepdoc。"""
from __future__ import annotations

from typing import Optional

from langchain_core.documents import Document

from kb.document_parse.deepdoc_result import DeepDocParseResult
from kb.document_parse.deepdoc_service import parse_file_with_deepdoc
from kb.document_parse.models import ParsedFile


class ParserFactory:
    @staticmethod
    def parse(
        file_path: str,
        *,
        domain: Optional[str] = None,
        business: Optional[str] = None,
        parser_id: Optional[str] = None,
    ) -> ParsedFile:
        effective = (parser_id or "deepdoc").strip().lower()
        if effective != "deepdoc":
            raise ValueError(f"仅支持 parser_id=deepdoc，收到: {effective}")

        result = parse_file_with_deepdoc(file_path, domain=domain, business=business)
        return ParserFactory._to_parsed_file(result)

    @staticmethod
    def from_deepdoc_result(result: DeepDocParseResult) -> ParsedFile:
        return ParserFactory._to_parsed_file(result)

    @staticmethod
    def _to_parsed_file(result: DeepDocParseResult) -> ParsedFile:
        markdown = result.to_markdown()
        row_documents = None
        if result.blocks and all(b.layout_type == "table_row" for b in result.blocks):
            row_documents = [
                Document(
                    page_content=block.content,
                    metadata={
                        "file_name": result.source_file_name,
                        "source": result.file_path,
                        "file_type": result.file_type,
                        "element_type": "table",
                        "raw_text": block.content,
                        "clean_text": block.content,
                        "update_time": result.update_time,
                        "row": idx,
                    },
                )
                for idx, block in enumerate(result.blocks)
            ]
        return ParsedFile(
            file_path=result.file_path,
            file_name=result.source_file_name,
            file_type=result.file_type,
            update_time=result.update_time,
            domain=result.domain,
            business=result.business,
            raw_markdown=markdown,
            clean_markdown=markdown,
            row_documents=row_documents,
            deepdoc_result=result,
        )
