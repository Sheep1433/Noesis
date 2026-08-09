"""Bind host-specific observability callbacks to the core runtime."""

from __future__ import annotations

from server import langfuse as lf
from noesis.runtime.deps import bind_langfuse


def wire_runtime_observability() -> None:
    """Bind Langfuse callbacks implemented by the HTTP host."""
    bind_langfuse(
        tracing_enabled=lf.langfuse_tracing_enabled,
        merge_runnable_config=lf.merge_langfuse_runnable_config,
        hits_to_payload=lf.hits_to_langfuse_payload,
        retrieval_observation=lf.langfuse_retrieval_observation,
        workflow_context=lf.langfuse_workflow_context,
    )
