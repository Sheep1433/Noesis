"""Knowledge 子系统运行时单例。"""
from __future__ import annotations

from noesis.knowledge.factory import KnowledgeBaseFactory
from noesis.knowledge.implementations.qdrant import QdrantService
from noesis.knowledge.manager import KnowledgeBaseManager

KnowledgeBaseFactory.register(QdrantService)
knowledge_base = KnowledgeBaseManager()


async def init_knowledge_base() -> bool:
    """初始化 Knowledge client。"""
    return await knowledge_base.initialize()


async def close_knowledge_base() -> None:
    """关闭 Knowledge client。"""
    await knowledge_base.close()
