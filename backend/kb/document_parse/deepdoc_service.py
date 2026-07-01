"""DeepDoc 解析实现（唯一知识库解析路径）。"""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from common.logging import logger

from kb.document_parse.deepdoc_bootstrap import ensure_deepdoc_bootstrap
from kb.document_parse.deepdoc_config import DEEPDOC_UPSTREAM_COMMIT, models_available
from kb.document_parse.deepdoc_result import DeepDocBlock, DeepDocParseResult, DeepDocTable

_MARKDOWN_EXTENSIONS = frozenset({".md", ".markdown", ".mdown"})
_WORD_EXTENSIONS = frozenset({".docx", ".doc"})
_EXCEL_EXTENSIONS = frozenset({".xlsx", ".xls"})
_PDF_EXTENSIONS = frozenset({".pdf"})
_PPT_EXTENSIONS = frozenset({".pptx", ".ppt"})
_TEXT_EXTENSIONS = frozenset({".txt", ".csv"})


class DeepDocUnavailableError(RuntimeError):
    pass


def _require_models_for_pdf() -> None:
    if not models_available():
        raise DeepDocUnavailableError(
            "DeepDoc 模型权重未就绪。请运行: "
            "cd backend && uv run python -m kb.download_models"
        )


def _read_markdown_file(file_path: str) -> str:
    text: Optional[str] = None
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            with open(file_path, "r", encoding=enc) as handle:
                text = handle.read()
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        with open(file_path, "rb") as handle:
            text = handle.read().decode("utf-8", errors="replace")
    return text or ""


def _bbox_from_box(box: dict) -> Optional[List[float]]:
    positions = box.get("positions") or box.get("position")
    if isinstance(positions, list) and positions:
        pos = positions[0]
        if isinstance(pos, (list, tuple)) and len(pos) >= 5:
            return [float(x) for x in pos[1:5]]
    for key in ("x0", "x1", "top", "bottom"):
        if key not in box:
            return None
    return [float(box["x0"]), float(box["top"]), float(box["x1"]), float(box["bottom"])]


def _page_from_box(box: dict) -> Optional[int]:
    positions = box.get("positions") or box.get("position")
    if isinstance(positions, list) and positions:
        pos = positions[0]
        if isinstance(pos, (list, tuple)) and pos:
            try:
                return int(pos[0])
            except (TypeError, ValueError):
                return None
    page = box.get("page_no") or box.get("page_number")
    if page is not None:
        try:
            return int(page)
        except (TypeError, ValueError):
            return None
    return None


def _parse_pdf(file_path: str) -> Tuple[List[DeepDocBlock], List[DeepDocTable], List[DeepDocBlock]]:
    _require_models_for_pdf()
    mod = _load_deepdoc_parser_module("pdf_parser")
    parser = mod.RAGFlowPdfParser()
    bboxes = parser.parse_into_bboxes(file_path)
    blocks: List[DeepDocBlock] = []
    tables: List[DeepDocTable] = []
    figures: List[DeepDocBlock] = []
    for box in bboxes or []:
        if not isinstance(box, dict):
            continue
        text = (box.get("text") or "").strip()
        if not text:
            continue
        layout = (box.get("layout_type") or box.get("layoutno") or "text").lower()
        page_no = _page_from_box(box)
        bbox = _bbox_from_box(box)
        if layout in {"table", "table caption"}:
            tables.append(DeepDocTable(content=text, page_no=page_no, metadata={"layout_type": layout}))
        elif layout in {"figure", "figure caption", "image"}:
            figures.append(
                DeepDocBlock(content=text, page_no=page_no, layout_type=layout, bbox=bbox)
            )
        else:
            blocks.append(
                DeepDocBlock(content=text, page_no=page_no, layout_type=layout, bbox=bbox)
            )
    return blocks, tables, figures


def _parse_docx(file_path: str) -> Tuple[List[DeepDocBlock], List[DeepDocTable], List[DeepDocBlock]]:
    mod = _load_deepdoc_parser_module("docx_parser")
    parser = mod.RAGFlowDocxParser()
    secs, tbls = parser(file_path)
    blocks = [
        DeepDocBlock(
            content=(text or "").strip(),
            layout_type=(style or "paragraph").lower(),
            metadata={"style": style},
        )
        for text, style in (secs or [])
        if (text or "").strip()
    ]
    tables: List[DeepDocTable] = []
    for group in tbls or []:
        if isinstance(group, str):
            lines = [group]
        else:
            lines = list(group or [])
        for line in lines:
            text = (line or "").strip()
            if text:
                tables.append(
                    DeepDocTable(content=text, metadata={"source": "docx_table"})
                )
    return blocks, tables, []


def _load_deepdoc_parser_module(module_name: str):
    """按文件加载 deepdoc.parser 子模块，避免 parser/__init__ 拉取 PDF/xgboost。"""
    ensure_deepdoc_bootstrap()
    kb_dir = Path(__file__).resolve().parents[1]
    path = kb_dir / "deepdoc" / "parser" / f"{module_name}.py"
    full_name = f"deepdoc.parser.{module_name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(full_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {full_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


def _parse_excel(file_path: str) -> List[DeepDocBlock]:
    mod = _load_deepdoc_parser_module("excel_parser")
    with open(file_path, "rb") as handle:
        payload = handle.read()
    lines = mod.RAGFlowExcelParser()(payload)
    return [
        DeepDocBlock(content=(line or "").strip(), layout_type="table_row")
        for line in (lines or [])
        if (line or "").strip()
    ]


def _parse_ppt(file_path: str) -> List[DeepDocBlock]:
    mod = _load_deepdoc_parser_module("ppt_parser")
    slides = mod.RAGFlowPptParser()(file_path, 0, 10_000)
    return [
        DeepDocBlock(content=(slide or "").strip(), layout_type="slide", page_no=idx + 1)
        for idx, slide in enumerate(slides or [])
        if (slide or "").strip()
    ]


def _parse_txt(file_path: str) -> List[DeepDocBlock]:
    mod = _load_deepdoc_parser_module("txt_parser")
    chunks = mod.RAGFlowTxtParser()(file_path, chunk_token_num=128)
    blocks: List[DeepDocBlock] = []
    for item in chunks or []:
        if isinstance(item, (list, tuple)) and item:
            text = str(item[0])
        else:
            text = str(item)
        text = text.strip()
        if text:
            blocks.append(DeepDocBlock(content=text, layout_type="text"))
    return blocks


def parse_file_with_deepdoc(
    file_path: str,
    *,
    domain: Optional[str] = None,
    business: Optional[str] = None,
) -> DeepDocParseResult:
    ensure_deepdoc_bootstrap()
    ext = os.path.splitext(file_path)[-1].lower()
    file_name = os.path.basename(file_path)
    file_type = ext.lstrip(".")
    update_time = datetime.now().isoformat()

    blocks: List[DeepDocBlock] = []
    tables: List[DeepDocTable] = []
    figures: List[DeepDocBlock] = []

    if ext in _PDF_EXTENSIONS:
        blocks, tables, figures = _parse_pdf(file_path)
    elif ext in _WORD_EXTENSIONS:
        blocks, tables, figures = _parse_docx(file_path)
    elif ext in _EXCEL_EXTENSIONS:
        blocks = _parse_excel(file_path)
    elif ext in _PPT_EXTENSIONS:
        blocks = _parse_ppt(file_path)
    elif ext in _MARKDOWN_EXTENSIONS:
        text = _read_markdown_file(file_path)
        if text.strip():
            blocks = [DeepDocBlock(content=text, layout_type="markdown")]
    elif ext in _TEXT_EXTENSIONS:
        blocks = _parse_txt(file_path)
    else:
        raise ValueError(f"DeepDoc 暂不支持的文件格式: {ext or file_path}")

    if not blocks and not tables and not figures:
        raise ValueError(f"DeepDoc 解析失败或内容为空: {ext or file_path}")

    return DeepDocParseResult(
        source_file_name=file_name,
        file_path=file_path,
        file_type=file_type,
        blocks=blocks,
        tables=tables,
        figures=figures,
        parser_id="deepdoc",
        deepdoc_version=DEEPDOC_UPSTREAM_COMMIT[:12],
        update_time=update_time,
        domain=domain,
        business=business,
    )
