"""Wire platform services into harness deps (one-way: platform → harness).

LLM kit is a shared package (``llm``) imported directly by harness — not bound here.
KB retrieval / Qdrant / VLM / collection-config are now imported directly by
harness from ``noesis.knowledge`` / ``noesis.repositories`` / ``noesis.storage``;
no KB binding is needed here. Only attachments / memory / Langfuse remain bound.
"""

from __future__ import annotations

from noesis_server.infrastructure.observability import langfuse as lf
from noesis.runtime.deps import (
    bind_attachment_service,
    bind_langfuse,
    bind_memory_service,
)
from noesis.services.chat_attachment_service import ChatAttachmentService
from noesis.services.memory_dream_service import MemoryDreamService
from noesis.services.user_memory_service import UserMemoryService


def wire_harness_platform_deps() -> None:
    """Bind ChatAttachment / Memory / Langfuse for noesis."""
    bind_attachment_service(ChatAttachmentService)

    class MemoryPlatformService:
        search_entries = staticmethod(UserMemoryService.search_entries)
        get_source = staticmethod(MemoryDreamService.get_source)

    bind_memory_service(MemoryPlatformService)
    bind_langfuse(
        tracing_enabled=lf.langfuse_tracing_enabled,
        merge_runnable_config=lf.merge_langfuse_runnable_config,
        hits_to_payload=lf.hits_to_langfuse_payload,
        retrieval_observation=lf.langfuse_retrieval_observation,
    )
