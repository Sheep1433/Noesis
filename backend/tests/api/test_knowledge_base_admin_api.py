"""知识库管理接口用例（integration）：状态/集合 CRUD/配置/文档/检索 happy path。

此前仅 upload / 建删集合被当作 citation fixture 的 setup/teardown 顺带触达，
本文件对每个端点自身行为断言。集合名随机生成，测试结束删除。

前置与运行：

    cd backend && uv run app.py
    uv run pytest tests/api/test_knowledge_base_admin_api.py -m integration
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.integration]

_DOC_TEXT = """# 接口验证文档

向量数据库是检索增强生成的存储底座。
Qdrant 是本仓库使用的向量数据库，支持 HNSW 索引与 payload 过滤。
这条文档用于知识库检索接口的集成验证。
"""


def _unique_collection() -> str:
    return f"api-kb-{uuid.uuid4().hex[:8]}"


def test_kb_status_and_collections_list(auth_client) -> None:
    resp = auth_client.get("/api/knowledge_base/status")
    resp.raise_for_status()
    assert isinstance(resp.json()["data"], dict)

    resp = auth_client.get("/api/knowledge_base/collections")
    resp.raise_for_status()
    data = resp.json()["data"]
    assert isinstance(data, (list, dict))


def test_collection_lifecycle_config_documents_and_search(auth_client) -> None:
    """建集合→详情→配置读写→上传→文档列表→检索→删文档→删集合。"""
    name = _unique_collection()
    try:
        resp = auth_client.post(
            "/api/knowledge_base/collections",
            json={"name": name, "vector_dimension": 1024},
        )
        resp.raise_for_status()

        resp = auth_client.get(f"/api/knowledge_base/collections/{name}")
        resp.raise_for_status()
        assert isinstance(resp.json()["data"], dict)

        resp = auth_client.get(f"/api/knowledge_base/collections/{name}/config")
        resp.raise_for_status()
        assert isinstance(resp.json()["data"], dict)

        resp = auth_client.put(
            f"/api/knowledge_base/collections/{name}/config",
            json={"query_params": {"final_top_k": 3}},
        )
        resp.raise_for_status()

        resp = auth_client.post(
            f"/api/knowledge_base/collections/{name}/upload",
            files={"file": ("verify-doc.md", _DOC_TEXT.encode("utf-8"), "text/markdown")},
        )
        resp.raise_for_status()
        upload_data = resp.json()["data"]
        assert upload_data.get("shards_created", 0) > 0, "入库应产生分片"

        resp = auth_client.get(f"/api/knowledge_base/collections/{name}/documents")
        resp.raise_for_status()
        documents = resp.json()["data"]
        doc_items = documents.get("documents") if isinstance(documents, dict) else documents
        assert any(
            (d.get("file_name") if isinstance(d, dict) else d) == "verify-doc.md"
            for d in (doc_items or [])
        ), "上传文档应出现在文档列表"

        resp = auth_client.post(
            f"/api/knowledge_base/collections/{name}/search",
            json={"query": "本仓库使用哪个向量数据库？", "final_top_k": 3},
        )
        resp.raise_for_status()
        results = resp.json()["data"]
        result_items = results.get("results") if isinstance(results, dict) else results
        assert result_items, "含关键词的检索应返回命中"

        resp = auth_client.delete(
            f"/api/knowledge_base/collections/{name}/documents/verify-doc.md"
        )
        resp.raise_for_status()
    finally:
        auth_client.delete(f"/api/knowledge_base/collections/{name}")
