"""Wire platform services into harness deps (one-way: platform → harness).

LLM kit is a shared package (``llm``) imported directly by harness — not bound here.
"""

from __future__ import annotations

from noesis_server.infrastructure.observability import langfuse as lf
from noesis.runtime.deps import (
    bind_attachment_service,
    bind_kb_retrieval,
    bind_kb_services,
    bind_langfuse,
    bind_vlm,
)
from noesis_server.kb.chunk import normalize_query_execution_params
from noesis_server.kb.embedding import is_vlm_configured
from noesis_server.kb.retrieval.service import KbRetrievalService
from noesis_server.services.chat_attachment_service import ChatAttachmentService
from noesis_server.services.kb_collection_config_service import KbCollectionConfigService
from noesis_server.kb.qdrant import QdrantService, is_qdrant_connected


def wire_harness_platform_deps() -> None:
    """Bind ChatAttachment / KB / Langfuse / VLM for noesis."""
    bind_attachment_service(ChatAttachmentService)
    bind_kb_services(
        collection_config_service=KbCollectionConfigService,
        qdrant_service_factory=QdrantService,
        is_qdrant_connected=is_qdrant_connected,
    )
    bind_kb_retrieval(
        normalize_query_execution_params=normalize_query_execution_params,
        retrieval_service=KbRetrievalService,
    )
    bind_vlm(is_vlm_configured)
    bind_langfuse(
        tracing_enabled=lf.langfuse_tracing_enabled,
        merge_runnable_config=lf.merge_langfuse_runnable_config,
        hits_to_payload=lf.hits_to_langfuse_payload,
        retrieval_observation=lf.langfuse_retrieval_observation,
    )
