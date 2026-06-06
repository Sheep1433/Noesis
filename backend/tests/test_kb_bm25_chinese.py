"""BM25 中文关键词检索回归（jieba 分词 + 负分不过滤）。"""
from langchain_core.documents import Document

from kb.retrieval import Retrieval, VectorStore, kb_bm25_preprocess


class _FakeVectorStore:
    collection_name = "test_col"

    def load_all_documents(self, limit: int = 10000):
        return [
            Document(
                page_content="用户打开登录页，系统生成验证码",
                metadata={"file_name": "login.md", "point_id": "p1"},
            ),
            Document(
                page_content="与登录无关的订单模块说明",
                metadata={"file_name": "order.md", "point_id": "p2"},
            ),
        ]


def test_kb_bm25_preprocess_splits_chinese_login():
    tokens = kb_bm25_preprocess("用户打开登录页")
    assert "登录" in tokens


def test_bm25_search_finds_login_keyword():
    retrieval = Retrieval(
        vector_store=_FakeVectorStore(),  # type: ignore[arg-type]
        auto_load_documents=False,
    )
    retrieval.documents = _FakeVectorStore().load_all_documents()
    retrieval._rebuild_bm25()

    scored = retrieval.bm25_search_with_scores("登录", k=5)
    assert scored, "关键词「登录」应能命中含登录的文档"
    assert any("登录" in (doc.page_content or "") for doc, _ in scored)
