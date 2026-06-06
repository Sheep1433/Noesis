"""
默认知识库初始化：确保 requirement_docs、test_case_docs 两个 Collection 存在。

启动时在 Qdrant 连接成功后调用；已存在则跳过。
"""
from __future__ import annotations

from config.env import QdrantConfig
from services.qdrant_service import QdrantService, is_qdrant_connected
from utils.log_util import logger

_VECTOR_DIM = 1024

_DEFAULT_COLLECTION_ATTRS = ("requirement_docs_collection", "test_case_docs_collection")


def _ensure_collection(service: QdrantService, collection_name: str) -> bool:
    result = service.create_collection(collection_name, vector_dimension=_VECTOR_DIM)
    if result.get("success"):
        logger.info(f"[KB Init] 创建 Collection: {collection_name}")
        return True
    if result.get("code") == 409:
        return True
    logger.warning(
        f"[KB Init] Collection {collection_name} 创建失败: {result.get('message')}"
    )
    return False


async def ensure_default_kb_collections() -> None:
    """确保默认知识库 Collection 存在：需求文档、历史测试用例。"""
    if not is_qdrant_connected():
        logger.warning("[KB Init] Qdrant 未连接，跳过默认知识库初始化")
        return

    service = QdrantService()
    if not service.client:
        logger.warning("[KB Init] Qdrant 客户端不可用，跳过默认知识库初始化")
        return

    for attr in _DEFAULT_COLLECTION_ATTRS:
        collection_name = (getattr(QdrantConfig, attr, None) or "").strip()
        if not collection_name:
            continue
        _ensure_collection(service, collection_name)

    logger.info("[KB Init] 默认知识库 Collection 检查完成")
