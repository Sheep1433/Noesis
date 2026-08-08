"""离线评测运行时依赖初始化（不走 FastAPI lifespan）。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from langgraph.checkpoint.memory import MemorySaver

from noesis.config.checkpointer import temporary_checkpointer
from noesis.runtime.deps import temporary_attachment_service


class _NoAttachments:
    async def session_has_attachments(self, *_args: object, **_kwargs: object) -> bool:
        return False


@asynccontextmanager
async def eval_runtime(*, no_attachments: bool = False) -> AsyncIterator[MemorySaver]:
    """Use an in-memory checkpointer without importing platform services.

    SuperAgent benchmarks can opt into a scoped no-attachment provider. Harbor uses
    the bare factory and needs no platform capability bindings at all.
    """
    checkpointer = MemorySaver()
    with temporary_checkpointer(checkpointer):
        if no_attachments:
            with temporary_attachment_service(_NoAttachments()):
                yield checkpointer
        else:
            yield checkpointer


@asynccontextmanager
async def agentic_rag_runtime() -> AsyncIterator[None]:
    """Initialize the KB engine + Postgres storage for Harness RAG tools.

    KB retrieval, Qdrant, VLM, and collection-config are imported directly by
    the harness tools from ``noesis.knowledge`` / ``noesis.repositories`` /
    ``noesis.storage``; no dependency injection is needed. This context only
    ensures Qdrant is connected and the Postgres engine is ready for the
    synchronous collection-config reads performed inside Agent tool threads.
    """
    from noesis.knowledge.implementations.qdrant import init_qdrant_client
    from noesis.storage.postgres.manager import pg_manager

    if not await init_qdrant_client():
        raise RuntimeError("Agentic RAG 评测需要可用的 Qdrant")
    pg_manager._ensure_engine()
    yield
