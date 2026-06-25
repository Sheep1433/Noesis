"""评测 fixture 文档加载测试。"""

from evals.case.fixtures import FIXTURES_DOCUMENTS_DIR, resolve_document_context


def test_fixtures_documents_exist():
    md_files = list(FIXTURES_DOCUMENTS_DIR.glob("tc_*.md"))
    assert len(md_files) >= 8


def test_resolve_document_context():
    item = {
        "id": "tc_login_001",
        "document_path": "fixtures/documents/tc_login_001.md",
    }
    text = resolve_document_context(item)
    assert "验证码" in text
