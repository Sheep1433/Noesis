"""Platform dependency binding for noesis.

Harness MUST NOT import ``services.*``, ``domain.*``, or ``models.*``.
LLM kit (``llm``) is a shared package and may be imported directly.

KB retrieval / Qdrant / VLM / collection-config are now imported directly by
harness from ``noesis.knowledge`` / ``noesis.repositories`` / ``noesis.storage``;
no KB binding surface remains here. Only attachments / memory / Langfuse are
bound by the platform host.
"""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any, Callable

_attachment_service: Any | None = None
_langfuse_tracing_enabled: Callable[[], bool] | None = None
_merge_langfuse_runnable_config: Callable[..., dict] | None = None
_hits_to_langfuse_payload: Callable[..., Any] | None = None
_langfuse_retrieval_observation: Callable[..., Any] | None = None
_memory_service: Any | None = None


def bind_attachment_service(service: Any) -> None:
    global _attachment_service
    _attachment_service = service


def bind_memory_service(service: Any) -> None:
    global _memory_service
    _memory_service = service


@contextmanager
def temporary_memory_service(service: Any) -> Iterator[None]:
    global _memory_service
    previous = _memory_service
    _memory_service = service
    try:
        yield
    finally:
        _memory_service = previous


def require_memory_service() -> Any:
    if _memory_service is None:
        raise RuntimeError("Memory service not bound; call noesis.runtime.deps.bind_memory_service")
    return _memory_service


@contextmanager
def temporary_attachment_service(service: Any) -> Iterator[None]:
    """Temporarily bind attachments for an embedded/eval runtime and restore afterwards."""
    global _attachment_service
    previous = _attachment_service
    _attachment_service = service
    try:
        yield
    finally:
        _attachment_service = previous


def bind_langfuse(
    *,
    tracing_enabled: Callable[[], bool],
    merge_runnable_config: Callable[..., dict],
    hits_to_payload: Callable[..., Any] | None = None,
    retrieval_observation: Callable[..., Any] | None = None,
) -> None:
    global _langfuse_tracing_enabled, _merge_langfuse_runnable_config
    global _hits_to_langfuse_payload, _langfuse_retrieval_observation
    _langfuse_tracing_enabled = tracing_enabled
    _merge_langfuse_runnable_config = merge_runnable_config
    _hits_to_langfuse_payload = hits_to_payload
    _langfuse_retrieval_observation = retrieval_observation


def require_attachment_service() -> Any:
    if _attachment_service is None:
        raise RuntimeError(
            "Chat attachment service not bound; call noesis.runtime.deps.bind_attachment_service "
            "from platform startup or eval bootstrap"
        )
    return _attachment_service


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
