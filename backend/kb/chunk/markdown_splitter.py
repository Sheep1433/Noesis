"""Markdown 分块算法（标题层级 + 表格保护 + 滑窗回退）。"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownHeaderTextSplitter

from common.logging import logger


class MarkdownChunker:
    @staticmethod
    def split_to_chunks(
        documents: List[Document], chunk_size: int = 1000, chunk_overlap: int = 200
    ) -> List[Document]:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )
        docs: List[Document] = splitter.split_documents(documents)
        logger.info(f"切分得到 {len(docs)} 个 chunks")
        return docs

    @staticmethod
    def _normalize_markdown_code_blocks(markdown_content: str) -> str:
        """
        规范化 Markdown 代码块格式，确保代码块中的 # 不会被误识别为标题
        
        处理策略：
        1. 识别围栏式代码块（``` 或 ~~~）
        2. 确保代码块标记独占一行（在标记前后添加换行符）
        3. 处理缩进代码块（连续4个空格开头的行）
        
        Args:
            markdown_content: 原始 Markdown 内容
            
        Returns:
            规范化后的 Markdown 内容
        """
        lines = markdown_content.split('\n')
        normalized_lines = []
        in_code_block = False
        code_fence = None  # 记录代码块的围栏符号（``` 或 ~~~）
        
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            
            # 检测代码块开始/结束
            if not in_code_block:
                # 检查是否是代码块开始
                if stripped.startswith('```'):
                    in_code_block = True
                    code_fence = '```'
                    # 确保代码块标记独占一行
                    if len(stripped) > 3 and not stripped[3:].replace('-', '').replace('_', '').isalnum():
                        # 包含非语言标识符的内容，可能格式不规范
                        pass
                    normalized_lines.append(line)
                elif stripped.startswith('~~~'):
                    in_code_block = True
                    code_fence = '~~~'
                    normalized_lines.append(line)
                else:
                    normalized_lines.append(line)
            else:
                # 在代码块内部，检查是否是结束标记
                if stripped.startswith(code_fence) and stripped.count(code_fence[0]) >= 3:
                    in_code_block = False
                    code_fence = None
                normalized_lines.append(line)
            
            i += 1
        
        return '\n'.join(normalized_lines)

    @staticmethod
    def split_markdown_with_headers(
        raw_markdown: str,
        clean_markdown: Optional[str] = None,
        source: Optional[str] = None,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
    ) -> List[Document]:
        """
        按照 Markdown 标题层级切分文档，并对超长部分进行二次切分。

        同时切分 raw_markdown 和 clean_markdown（若相同则只切一次），
        每个 chunk 的 metadata 包含：
          - raw_text: 原始内容（含 data URI 图片等）
          - clean_text: 清洗后内容（图片已替换为描述）
          - element_type: text / image / table
          - source, header_path, Header_n
        """
        if clean_markdown is None:
            clean_markdown = raw_markdown

        # 预处理：确保代码块中的 # 不被识别为标题
        # raw_markdown = MarkdownChunker._normalize_markdown_code_blocks(raw_markdown)
        # clean_markdown = MarkdownChunker._normalize_markdown_code_blocks(clean_markdown)

        headers_to_split_on = [
            ("#", "Header_1"),
            ("##", "Header_2"),
            ("###", "Header_3"),
        ]
        header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=headers_to_split_on,
            strip_headers=False,
        )

        raw_splits = header_splitter.split_text(raw_markdown)
        clean_splits = header_splitter.split_text(clean_markdown)
        logger.info(f"按标题切分得到 {len(raw_splits)} 个章节")

        # 若两份切分数量不一致（极少数情况），退化为只用 clean
        if len(raw_splits) != len(clean_splits):
            logger.warning("raw/clean 切分数量不一致，raw_text 将等于 clean_text")
            raw_splits = clean_splits

        final_chunks = []

        for raw_doc, clean_doc in zip(raw_splits, clean_splits):
            header_context = " > ".join([
                raw_doc.metadata.get(h[1], "")
                for h in headers_to_split_on
                if h[1] in raw_doc.metadata and raw_doc.metadata.get(h[1])
            ])

            if len(clean_doc.page_content) > chunk_size:
                tables = MarkdownChunker.extract_tables_from_markdown(clean_doc.page_content)
                if tables:
                    raw_sub = MarkdownChunker.split_content_with_table_protection(
                        raw_doc, MarkdownChunker.extract_tables_from_markdown(raw_doc.page_content),
                        chunk_size, chunk_overlap
                    )
                    clean_sub = MarkdownChunker.split_content_with_table_protection(
                        clean_doc, tables, chunk_size, chunk_overlap
                    )
                else:
                    splitter = RecursiveCharacterTextSplitter(
                        chunk_size=chunk_size,
                        chunk_overlap=chunk_overlap,
                        separators=["\n\n", "\n", " ", ""],
                    )
                    raw_sub = splitter.split_documents([raw_doc])
                    clean_sub = splitter.split_documents([clean_doc])

                if len(raw_sub) != len(clean_sub):
                    raw_sub = clean_sub

                for r, c in zip(raw_sub, clean_sub):
                    element_type = MarkdownChunker._detect_element_type(r.page_content)
                    r.metadata.update({
                        "header_path": header_context,
                        "raw_text": r.page_content,
                        "clean_text": c.page_content,
                        "element_type": element_type,
                    })
                    if source:
                        r.metadata["source"] = source
                    r.page_content = c.page_content  # page_content 用 clean 版本
                    final_chunks.append(r)

                logger.info(f"章节 '{header_context}' 超长，二次切分为 {len(clean_sub)} 个子块")
            else:
                element_type = MarkdownChunker._detect_element_type(raw_doc.page_content)
                raw_doc.metadata.update({
                    "header_path": header_context,
                    "raw_text": raw_doc.page_content,
                    "clean_text": clean_doc.page_content,
                    "element_type": element_type,
                })
                if source:
                    raw_doc.metadata["source"] = source
                raw_doc.page_content = clean_doc.page_content
                final_chunks.append(raw_doc)

        logger.info(f"最终得到 {len(final_chunks)} 个 chunks")
        return final_chunks

    @staticmethod
    def _detect_element_type(content: str) -> str:
        """根据内容特征判断 element_type：image / table / text"""
        if re.search(r'data:image/[^;]+;base64,', content):
            return "image"
        # 简单判断：内容中超过一半的行是表格行
        lines = [line for line in content.split("\n") if line.strip()]
        if lines:
            table_lines = sum(1 for line in lines if "|" in line)
            if table_lines / len(lines) >= 0.5:
                return "table"
        return "text"
    
    @staticmethod
    def split_content_with_table_protection(
        doc: Document,
        tables: List[Tuple[int, int, str]],
        chunk_size: int,
        chunk_overlap: int
    ) -> List[Document]:
        """
        在保护超长表格完整性的前提下切分文档内容
        
        策略：
        1. 小表格（<= chunk_size）：和周围文本一起正常切分，不做特殊处理
        2. 超长表格（> chunk_size）：单独作为一个 chunk（保持完整性），添加标记
        """
        content = doc.page_content
        
        # 筛选出真正需要保护的超长表格
        large_tables = [(start, end, tbl) for start, end, tbl in tables if len(tbl) > chunk_size]
        
        if not large_tables:
            # 没有超长表格，使用标准切分即可
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                separators=["\n\n", "\n", " ", ""]
            )
            return text_splitter.split_documents([doc])
        
        # 有超长表格，需要特殊处理
        chunks = []
        current_pos = 0
        
        for table_start, table_end, table_content in large_tables:
            # 处理表格之前的内容（包含小表格）
            if table_start > current_pos:
                before_table = content[current_pos:table_start].strip()
                if before_table:
                    # 对非超长表格内容进行标准切分（包括小表格）
                    text_splitter = RecursiveCharacterTextSplitter(
                        chunk_size=chunk_size,
                        chunk_overlap=chunk_overlap,
                        separators=["\n\n", "\n", " ", ""]
                    )
                    temp_doc = Document(page_content=before_table, metadata=doc.metadata.copy())
                    sub_chunks = text_splitter.split_documents([temp_doc])
                    chunks.extend(sub_chunks)
            
            # 处理超长表格（单独成块，保持完整）
            table_doc = Document(
                page_content=table_content.strip(),
                metadata={**doc.metadata, "contains_large_table": True}
            )
            chunks.append(table_doc)
            logger.info(f"超长表格 ({len(table_content)} 字符 > {chunk_size})，单独保存为一个 chunk")
            
            current_pos = table_end
        
        # 处理最后一个超长表格之后的内容
        if current_pos < len(content):
            after_tables = content[current_pos:].strip()
            if after_tables:
                text_splitter = RecursiveCharacterTextSplitter(
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    separators=["\n\n", "\n", " ", ""]
                )
                temp_doc = Document(page_content=after_tables, metadata=doc.metadata.copy())
                sub_chunks = text_splitter.split_documents([temp_doc])
                chunks.extend(sub_chunks)
        
        return chunks if chunks else [doc]

    @staticmethod
    def extract_tables_from_markdown(content: str) -> List[Tuple[int, int, str]]:
        """从 Markdown 内容中提取表格的位置和内容。"""
        tables = []
        lines = content.split("\n")
        i = 0

        while i < len(lines):
            line = lines[i].strip()

            if "|" in line and line.startswith("|") or (
                "|" in line and len([c for c in line if c == "|"]) >= 2
            ):
                table_start = i
                table_lines = [lines[i]]
                i += 1

                while i < len(lines):
                    current_line = lines[i].strip()
                    if "|" in current_line or re.match(r"^[\s\|\-:]+$", current_line):
                        table_lines.append(lines[i])
                        i += 1
                    elif not current_line:
                        table_lines.append(lines[i])
                        i += 1
                        if i < len(lines) and "|" not in lines[i]:
                            break
                    else:
                        break

                table_content = "\n".join(table_lines)
                start_pos = len("\n".join(lines[:table_start]))
                if table_start > 0:
                    start_pos += 1
                end_pos = start_pos + len(table_content)

                tables.append((start_pos, end_pos, table_content))
                logger.info(
                    f"检测到表格 (行 {table_start + 1}-{i}, {len(table_content)} 字符)"
                )
            else:
                i += 1

        return tables

