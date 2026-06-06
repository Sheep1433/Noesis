"""
文档解析工具
"""

import os
import re
from datetime import datetime
from typing import List, Tuple, Optional
import pandas as pd
from docx import Document as DocxDocument
from langchain_core.documents import Document

from utils.log_util import logger

_MARKDOWN_EXTENSIONS = frozenset({".md", ".markdown", ".mdown"})
_WORD_EXTENSIONS = frozenset({".docx", ".doc"})
_EXCEL_EXTENSIONS = frozenset({".xlsx", ".xls"})


class DocumentParser:
    @staticmethod
    def convert_file_to_markdown(file_path: str) -> str:
        """将文件转为 Markdown 文本；.md 直接读取，其余格式走 markitdown。"""
        ext = os.path.splitext(file_path)[1].lower()
        if ext in _MARKDOWN_EXTENSIONS:
            return DocumentParser._read_markdown_file(file_path)
        try:
            from markitdown import MarkItDown

            md = MarkItDown()
            result = md.convert(file_path)
            return result.text_content or ""
        except Exception as exc:
            logger.error(f"文档转 Markdown 失败 {file_path}: {exc}")
            return ""

    @staticmethod
    def _read_markdown_file(file_path: str) -> str:
        text: Optional[str] = None
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                with open(file_path, "r", encoding=enc) as f:
                    text = f.read()
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            with open(file_path, "rb") as f:
                text = f.read().decode("utf-8", errors="replace")
        return text or ""

    @staticmethod
    def _load_excel_rows(
        file_path: str,
        file_name: str,
        file_type: str,
        update_time: str,
    ) -> List[Document]:
        df = pd.read_excel(file_path)
        documents: List[Document] = []
        for index, row in df.iterrows():
            parts = [
                f"{col}: {val}"
                for col, val in row.items()
                if pd.notna(val) and str(val).strip()
            ]
            content = " ".join(parts).strip()
            if not content:
                continue
            documents.append(
                Document(
                    page_content=content,
                    metadata={
                        "file_name": file_name,
                        "source": file_path,
                        "file_type": file_type,
                        "element_type": "table",
                        "raw_text": content,
                        "clean_text": content,
                        "update_time": update_time,
                        "row": int(index),
                    },
                )
            )
        return documents

    @staticmethod
    def parse_file(
        file_path: str,
        domain: Optional[str] = None,
        business: Optional[str] = None,
        process_images: bool = False,
    ) -> "ParsedFile":
        """
        知识库统一文档解析入口（仅 parse，不分块）。

        - Excel/CSV：每行一个 Document（表格型 parse 结果）
        - Word/Markdown/其他：输出 raw/clean Markdown
        """
        from kb.document_parse.models import ParsedFile

        file_extension = os.path.splitext(file_path)[-1].lower()
        file_name = os.path.basename(file_path)
        file_type = file_extension.lstrip(".")
        update_time = datetime.now().isoformat()
        base = dict(
            file_path=file_path,
            file_name=file_name,
            file_type=file_type,
            update_time=update_time,
            domain=domain,
            business=business,
        )

        if file_extension in _EXCEL_EXTENSIONS:
            return ParsedFile(
                **base,
                row_documents=DocumentParser._load_excel_rows(
                    file_path, file_name, file_type, update_time
                ),
            )

        if file_extension in _WORD_EXTENSIONS:
            raw_markdown, clean_markdown = DocumentParser.enhance_docx_to_markdown(
                file_path, process_images=process_images
            )
            md_file_path = os.path.splitext(file_path)[0] + ".md"
            with open(md_file_path, "w", encoding="utf-8") as f:
                f.write(clean_markdown)
            return ParsedFile(
                **base,
                raw_markdown=raw_markdown,
                clean_markdown=clean_markdown,
            )

        if file_extension in _MARKDOWN_EXTENSIONS:
            raw_markdown = DocumentParser._read_markdown_file(file_path)
            return ParsedFile(
                **base,
                raw_markdown=raw_markdown,
                clean_markdown=raw_markdown,
            )

        if file_extension == ".csv":
            from langchain_community.document_loaders import CSVLoader

            loader = CSVLoader(file_path)
            documents = loader.load()
            for doc in documents:
                doc.metadata.update({
                    "file_name": file_name,
                    "source": file_path,
                    "file_type": "csv",
                    "element_type": "table",
                    "raw_text": doc.page_content,
                    "clean_text": doc.page_content,
                    "update_time": update_time,
                })
            return ParsedFile(**base, row_documents=documents)

        markdown = DocumentParser.convert_file_to_markdown(file_path)
        if not markdown.strip():
            raise ValueError(f"文档解析失败或内容为空: {file_extension or file_path}")
        return ParsedFile(
            **base,
            raw_markdown=markdown,
            clean_markdown=markdown,
        )

    @staticmethod
    def enhance_docx_to_markdown(file_path: str, process_images: bool = False) -> Tuple[str, str]:
        """
        将 docx 文件转换为 markdown，并清理目录区域。
        
        当 process_images=True 时，提取文档中的 data URI 图片，调用 VL 模型生成描述，
        将描述替换回 markdown 中，得到可检索的 clean_text。
        
        Returns:
            (raw_markdown, clean_markdown)
            raw_markdown: 保留 data URI 的原始 markdown（用于溯源）
            clean_markdown: 图片替换为描述后的 markdown（用于 RAG 检索）
        """
        DocumentParser.wrap_code_blocks_in_docx(file_path)
        from markitdown import MarkItDown

        if process_images:
            md = MarkItDown()
            result = md.convert(file_path, keep_data_uris=True)
        else:
            md = MarkItDown()
            result = md.convert(file_path)

        raw_markdown = result.text_content
        raw_markdown = DocumentParser.clean_table_of_contents(raw_markdown)

        if not process_images:
            return raw_markdown, raw_markdown

        # 提取所有 data URI 图片并用 VL 描述替换
        clean_markdown = DocumentParser._replace_images_with_descriptions(raw_markdown)
        return raw_markdown, clean_markdown

    @staticmethod
    def wrap_code_blocks_in_docx(file_path):
        doc = DocxDocument(file_path)
        in_code_block = False

        # 从后往前遍历，避免插入后索引变化影响后续处理
        for i in range(len(doc.paragraphs) - 1, -1, -1):
            para = doc.paragraphs[i]
            is_terminal = (para.style.name == 'Terminal Display')

            if is_terminal and not in_code_block:
                # 代码块开始：在当前 Terminal 段落前插入 ```
                para.insert_paragraph_before("```")
                in_code_block = True

            elif not is_terminal and in_code_block:
                # 代码块结束：在当前非 Terminal 段落前插入 ```
                para.insert_paragraph_before("```")
                in_code_block = False

        # 处理文档以 Terminal 结尾的情况（需要闭合代码块）
        if in_code_block:
            doc.paragraphs[0].insert_paragraph_before("```")

        doc.save(file_path)
        print(f"处理完成！已保存至: {file_path}")

    def _extract_data_uris(text: str) -> List[str]:
        """从 markdown 文本中提取所有 data URI"""
        pattern = r'data:image/[^;]+;base64,[A-Za-z0-9+/=]+'
        return re.findall(pattern, text)

    @staticmethod
    def _replace_images_with_descriptions(markdown_content: str) -> str:
        """
        将 markdown 中的 data URI 图片替换为 VL 模型生成的文字描述。
        找不到图片或调用失败时，替换为占位文本。
        """
        # 匹配 markdown 图片语法中的 data URI：![...](data:image/...;base64,...)
        img_pattern = re.compile(
            r'!\[([^\]]*)\]\((data:image/[^;]+;base64,[A-Za-z0-9+/=]+)\)'
        )

        def replace_one(match: re.Match) -> str:
            logger.warning("哈哈哈---------开始处理图片")
            alt_text = match.group(1)
            data_uri = match.group(2)
            logger.warning(f"发现 data URI 图片，alt='{alt_text}', 大小={len(data_uri)} 字节")
            try:
                description = DocumentParser._call_vl_model(data_uri)
                logger.info(f"图片描述生成成功，alt='{alt_text}'，描述长度={len(description)}")
                return f"\n\n[图片描述]: {description}\n\n"
            except BaseException as e:
                logger.warning(f"VL 模型调用失败，使用占位文本: {type(e).__name__}: {e}")
                return f"\n\n[图片描述]: （图片内容，alt={alt_text}）\n\n"

        return img_pattern.sub(replace_one, markdown_content)

    @staticmethod
    def _call_vl_model(data_uri: str) -> str:
        """调用 VL 模型对图片生成文字描述（mermaid 或自然语言）"""
        import httpx
        from openai import OpenAI
        from markitdown import MarkItDown
        from utils.proxy_util import set_proxy
        from config.env import ModelConfig

        set_proxy()
        api_key = (os.getenv("VL_MODEL_API_KEY") or "").strip() or ModelConfig.model_api_key
        if not api_key:
            raise ValueError(
                "VL 模型需要配置 MODEL_API_KEY 或 VL_MODEL_API_KEY（见 backend/.env.example）"
            )
        logger.info(f"开始调用 VL 模型，data_uri 大小={len(data_uri)} 字节")
        client = OpenAI(
            api_key=api_key,
            base_url=ModelConfig.model_base_url,
            http_client=httpx.Client(
                verify=False,
                timeout=httpx.Timeout(connect=10, read=120, write=30, pool=10),
            ),
        )
        vl_model = os.getenv("VL_MODEL_NAME", "Qwen3-VL-32B-Instruct")
        logger.info(f"使用 VL 模型: {vl_model}")
        md = MarkItDown(
            llm_client=client,
            llm_model=vl_model,
            llm_prompt="请用中文描述这张图片。如果是时序图或者其他适合用mermaid描述的图表，请使用mermaid描述其表达的技术逻辑。",
        )
        result = md.convert(data_uri)
        logger.info(f"VL 模型返回结果，长度={len(result.text_content or '')}")
        return result.text_content
    
    @staticmethod
    def clean_table_of_contents(markdown_content: str) -> str:
        """
        清理 Markdown 内容中的目录区域和目录链接
        
        策略：
        1. 直接删除所有包含 #_Toc 的目录链接行（更激进的清理）
        2. 检测连续的目录行并整体删除
        """
        lines = markdown_content.split('\n')
        
        # 第一步：定义 TOC 链接模式
        toc_pattern = r'\[.+?\]\(#_Toc\d+\)'
        
        # 第二步：统计并清理包含 TOC 链接的行
        cleaned_lines = []
        toc_link_count = 0
        
        for i, line in enumerate(lines):
            # 检测该行是否包含 TOC 链接
            if re.search(toc_pattern, line):
                toc_link_count += len(re.findall(toc_pattern, line))
                # 完全跳过包含 TOC 链接的行
                continue
            
            # 保留非 TOC 行
            cleaned_lines.append(line)
        
        if toc_link_count > 0:
            logger.info(f"共清理 {toc_link_count} 个目录链接")
        
        return '\n'.join(cleaned_lines)
