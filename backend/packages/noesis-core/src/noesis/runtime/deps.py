"""Runtime overrides for embedded evaluation and host observability."""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any, Callable

_attachment_service_override: Any | None = None
_langfuse_tracing_enabled: Callable[[], bool] | None = None
_merge_langfuse_runnable_config: Callable[..., dict] | None = None
_hits_to_langfuse_payload: Callable[..., Any] | None = None
_langfuse_retrieval_observation: Callable[..., Any] | None = None
_langfuse_workflow_context: Callable[..., Any] | None = None


@contextmanager
def temporary_attachment_service(service: Any) -> Iterator[None]:
    """Temporarily replace attachment access for an embedded/eval runtime."""
    global _attachment_service_override
    previous = _attachment_service_override
    _attachment_service_override = service
    try:
        yield
    finally:
        _attachment_service_override = previous


def bind_langfuse(
    *,
    tracing_enabled: Callable[[], bool],
    merge_runnable_config: Callable[..., dict],
    hits_to_payload: Callable[..., Any] | None = None,
    retrieval_observation: Callable[..., Any] | None = None,
    workflow_context: Callable[..., Any] | None = None,
) -> None:
    global _langfuse_tracing_enabled, _merge_langfuse_runnable_config
    global _hits_to_langfuse_payload, _langfuse_retrieval_observation, _langfuse_workflow_context
    _langfuse_tracing_enabled = tracing_enabled
    _merge_langfuse_runnable_config = merge_runnable_config
    _hits_to_langfuse_payload = hits_to_payload
    _langfuse_retrieval_observation = retrieval_observation
    _langfuse_workflow_context = workflow_context


def require_attachment_service() -> Any:
    if _attachment_service_override is not None:
        return _attachment_service_override
    from noesis.services.chat_attachment_service import ChatAttachmentService

    return ChatAttachmentService


def langfuse_tracing_enabled() -> bool:
    if _langfuse_tracing_enabled is None:
        return False
    return bool(_langfuse_tracing_enabled())


def merge_langfuse_runnable_config(config: dict, **kwargs: Any) -> dict:
    if _merge_langfuse_runnable_config is None:
        return config
    return _merge_langfuse_runnable_config(config, **kwargs)


def hits_to_langfuse_payload(*args: Any, **kwargs: Any) -> Any:
    if _hits_to_langfuse_payload is None:
        return None
    return _hits_to_langfuse_payload(*args, **kwargs)


def langfuse_retrieval_observation(*args: Any, **kwargs: Any) -> Any:
    if _langfuse_retrieval_observation is None:
        from contextlib import nullcontext

        return nullcontext()
    return _langfuse_retrieval_observation(*args, **kwargs)


def langfuse_workflow_context(config: dict) -> Any:
    if _langfuse_workflow_context is None:
        from contextlib import nullcontext

        return nullcontext()
    return _langfuse_workflow_context(config)
