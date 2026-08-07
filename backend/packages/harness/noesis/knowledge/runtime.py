"""Knowledge base runtime — startup assembly singleton.

Phase B: exposes ``knowledge_base`` as the Qdrant implementation entry point
for platform HTTP / tools. Phase C replaces this with ``KnowledgeBaseManager``
owning client lifecycle and domain logic (create/upload/search/delete) with
domain exceptions.
"""
from __future__ import annotations

from noesis.knowledge.implementations.qdrant import QdrantService, is_qdrant_connected

# Phase B transitional singleton: the Qdrant service instance.
# Phase C wraps this in KnowledgeBaseManager with repository-backed config.
knowledge_base = QdrantService()


def init_knowledge_base() -> None:
    """Initialize the knowledge base subsystem (client lifecycle).

    Phase B: no-op (Qdrant client managed by module-level init/close in
    ``implementations.qdrant``). Phase C: manager.initialize().
    """
    # Qdrant client init/close currently lives in implementations.qdrant via
    # init_qdrant_client / close_qdrant_client, called by platform lifespan.
    # Phase C moves those into the manager.
    return None


def close_knowledge_base() -> None:
    """Shutdown the knowledge base subsystem."""
    from noesis.knowledge.implementations.qdrant import close_qdrant_client

    close_qdrant_client()
