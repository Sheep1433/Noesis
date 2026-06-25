"""评测 fixture：需求文档路径解析。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

CASE_ROOT = Path(__file__).resolve().parent
PROMPTFOO_DIR = CASE_ROOT / "promptfoo"
FIXTURES_DOCUMENTS_DIR = PROMPTFOO_DIR / "fixtures" / "documents"


def resolve_document_context(item: Dict[str, Any]) -> str:
    if item.get("document_context"):
        return str(item["document_context"])

    doc_path = item.get("document_path")
    if not doc_path:
        return ""

    rel = Path(str(doc_path))
    if rel.is_absolute():
        full = rel
    elif rel.parts[:2] == ("fixtures", "documents"):
        full = (PROMPTFOO_DIR / rel).resolve()
    elif rel.parts[0] == "documents":
        full = (FIXTURES_DOCUMENTS_DIR / Path(*rel.parts[1:])).resolve()
    else:
        full = (FIXTURES_DOCUMENTS_DIR / rel).resolve()

    if not full.is_file():
        raise FileNotFoundError(f"文档不存在: {full}")
    return full.read_text(encoding="utf-8")
