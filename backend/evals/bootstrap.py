"""离线评测运行时依赖初始化（不走 FastAPI lifespan）。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from langgraph.checkpoint.memory import MemorySaver

from noesis.config.checkpointer import temporary_checkpointer
from noesis.runtime.deps import temporary_attachment_service, temporary_kb_runtime


class _NoAttachments:
    async def session_has_attachments(self, *_args: object, **_kwargs: object) -> bool:
        return False


@asynccontextmanager
async def eval_runtime(*, no_attachments: bool = False) -> AsyncIterator[None]:
    """Use an in-memory checkpointer without importing platform services.

    SuperAgent benchmarks can opt into a scoped no-attachment provider. Harbor uses
    the bare factory and needs no platform capability bindings at all.
    """
    with temporary_checkpointer(MemorySaver()):
        if no_attachments:
            with temporary_attachment_service(_NoAttachments()):
                yield
        else:
            yield


@asynccontextmanager
async def agentic_rag_runtime() -> AsyncIterator[None]:
    """Bind only the platform KB adapters required by the Harness RAG tools."""
    from noesis_server.kb.chunk import normalize_query_execution_params
    from noesis_server.kb.qdrant import (
        QdrantService,
        init_qdrant_client,
        is_qdrant_connected,
    )
    from noesis_server.kb.retrieval import KbRetrievalService
    from noesis_server.services.kb_collection_config_service import (
        KbCollectionConfigService,
    )

    if not await init_qdrant_client():
        raise RuntimeError("Agentic RAG 评测需要可用的 Qdrant")
    with temporary_kb_runtime(
        collection_config_service=KbCollectionConfigService,
        qdrant_service_factory=QdrantService,
        is_qdrant_connected=is_qdrant_connected,
        normalize_query_execution_params=normalize_query_execution_params,
        retrieval_service=KbRetrievalService,
    ):
        yield
