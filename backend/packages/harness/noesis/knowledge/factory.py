"""Knowledge base factory — registry of backend implementations.

Qdrant is the sole implementation. ``QdrantService`` is registered as the
``KnowledgeBase`` implementation in ``runtime``; full ABC inheritance and
manager ownership of client lifecycle land in Phase C.
"""
from __future__ import annotations

from typing import Any, Type

from noesis.knowledge.base import KnowledgeBase, KnowledgeBaseException


class KnowledgeBaseFactory:
    """知识库工厂：按 ``kb_type`` 注册与创建实现。"""

    _kb_types: dict[str, Type[KnowledgeBase]] = {}

    @classmethod
    def register(cls, kb_class: Type[KnowledgeBase]) -> None:
        if not issubclass(kb_class, KnowledgeBase):
            raise KnowledgeBaseException(f"{kb_class} must inherit KnowledgeBase")
        if not kb_class.kb_type:
            raise KnowledgeBaseException(f"{kb_class} must define kb_type")
        cls._kb_types[kb_class.kb_type] = kb_class

    @classmethod
    def create(cls, kb_type: str, *args: Any, **kwargs: Any) -> KnowledgeBase:
        kb_class = cls._kb_types.get(kb_type)
        if kb_class is None:
            available = list(cls._kb_types.keys())
            raise KnowledgeBaseException(
                f"Unknown knowledge base type: {kb_type}. Available: {available}"
            )
        return kb_class(*args, **kwargs)

    @classmethod
    def get_available_types(cls) -> dict[str, Type[KnowledgeBase]]:
        return dict(cls._kb_types)
