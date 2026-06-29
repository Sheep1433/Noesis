"""评测文档 fixture 测试。"""

from pathlib import Path

from evals.case.shared.provider_common import resolve_document_context

_TESTPOINTS_DIR = Path(__file__).resolve().parents[1] / "evals" / "case" / "testpoints"


def test_documents_exist():
    assert len(list((_TESTPOINTS_DIR / "documents").glob("prd_*.md"))) == 20


def test_resolve_document_context():
    text = resolve_document_context(
        {"document_path": "documents/prd_001.md"},
        base_dir=_TESTPOINTS_DIR,
    )
    assert "验证码" in text
