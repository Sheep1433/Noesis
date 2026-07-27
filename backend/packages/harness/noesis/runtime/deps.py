"""Platform dependency binding for noesis.

Harness MUST NOT import ``services.*``, ``domain.*``, or ``models.*``.
LLM kit (``llm``) is a shared package and may be imported directly.
Platform (app lifespan / evals bootstrap) binds KB / attachments / Langfuse / VLM here.
"""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any, Callable

_attachment_service: Any | None = None
_kb_collection_config_service: Any | None = None
_qdrant_service_factory: Callable[[], Any] | None = None
_is_qdrant_connected: Callable[[], bool] | None = None
_is_vlm_configured: Callable[[], bool] | None = None
_normalize_query_execution_params: Callable[..., Any] | None = None
_kb_retrieval_service: Any | None = None
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


def bind_kb_services(
    *,
    collection_config_service: Any,
    qdrant_service_factory: Callable[[], Any],
    is_qdrant_connected: Callable[[], bool],
) -> None:
    global _kb_collection_config_service, _qdrant_service_factory, _is_qdrant_connected
    _kb_collection_config_service = collection_config_service
    _qdrant_service_factory = qdrant_service_factory
    _is_qdrant_connected = is_qdrant_connected


def bind_vlm(is_vlm_configured: Callable[[], bool]) -> None:
    global _is_vlm_configured
    _is_vlm_configured = is_vlm_configured


def bind_kb_retrieval(
    *,
    normalize_query_execution_params: Callable[..., Any],
    retrieval_service: Any,
) -> None:
    """Bind KB retrieval primitives (no static ``kb`` import inside harness tools)."""
    global _normalize_query_execution_params, _kb_retrieval_service
    _normalize_query_execution_params = normalize_query_execution_params
    _kb_retrieval_service = retrieval_service


@contextmanager
def temporary_kb_runtime(
    *,
    collection_config_service: Any,
    qdrant_service_factory: Callable[[], Any],
    is_qdrant_connected: Callable[[], bool],
    normalize_query_execution_params: Callable[..., Any],
    retrieval_service: Any,
) -> Iterator[None]:
    """Temporarily bind all KB ports for an embedded or evaluation runtime."""
    global _kb_collection_config_service, _qdrant_service_factory, _is_qdrant_connected
    global _normalize_query_execution_params, _kb_retrieval_service
    previous = (
        _kb_collection_config_service,
        _qdrant_service_factory,
        _is_qdrant_connected,
        _normalize_query_execution_params,
        _kb_retrieval_service,
    )
    bind_kb_services(
        collection_config_service=collection_config_service,
        qdrant_service_factory=qdrant_service_factory,
        is_qdrant_connected=is_qdrant_connected,
    )
    bind_kb_retrieval(
        normalize_query_execution_params=normalize_query_execution_params,
        retrieval_service=retrieval_service,
    )
    try:
        yield
    finally:
        (
            _kb_collection_config_service,
            _qdrant_service_factory,
            _is_qdrant_connected,
            _normalize_query_execution_params,
            _kb_retrieval_service,
        ) = previous


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


def require_kb_collection_config_service() -> Any:
    if _kb_collection_config_service is None:
        raise RuntimeError(
            "KbCollectionConfigService not bound; call noesis.runtime.deps.bind_kb_services"
        )
    return _kb_collection_config_service


def require_qdrant_service() -> Any:
    if _qdrant_service_factory is None:
        raise RuntimeError("QdrantService factory not bound; call noesis.runtime.deps.bind_kb_services")
    return _qdrant_service_factory()


def require_is_qdrant_connected() -> bool:
    if _is_qdrant_connected is None:
        raise RuntimeError("is_qdrant_connected not bound; call noesis.runtime.deps.bind_kb_services")
    return bool(_is_qdrant_connected())


def require_is_vlm_configured() -> bool:
    if _is_vlm_configured is None:
        raise RuntimeError("is_vlm_configured not bound; call noesis.runtime.deps.bind_vlm")
    return bool(_is_vlm_configured())


def require_normalize_query_execution_params() -> Callable[..., Any]:
    if _normalize_query_execution_params is None:
        raise RuntimeError(
            "normalize_query_execution_params not bound; call noesis.runtime.deps.bind_kb_retrieval"
        )
    return _normalize_query_execution_params


def require_kb_retrieval_service() -> Any:
    if _kb_retrieval_service is None:
        raise RuntimeError(
            "KbRetrievalService not bound; call noesis.runtime.deps.bind_kb_retrieval"
        )
    return _kb_retrieval_service


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
