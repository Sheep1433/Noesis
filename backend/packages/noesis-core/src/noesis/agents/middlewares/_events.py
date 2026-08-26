"""Shared custom-event emission for Noesis middlewares.

Middlewares emit out-of-band custom events (retry / fallback / compaction
progress) that the SSE bridge translates into run-status / error frames. The
emission contract is identical across emitters:

- ``get_stream_writer()`` payload is attempted first (LangGraph stream channel);
  a writer failure must never abort the model call, so it is swallowed at
  debug level.
- ``dispatch_custom_event`` / ``adispatch_custom_event`` then fires the named
  event so LangGraph ``on_custom_event`` handlers (the bridge) receive it.
- ``GraphBubbleUp`` (LangGraph control-flow: ``GraphInterrupt`` / HITL) is
  always re-raised; only ordinary failures are swallowed.
"""

from __future__ import annotations

from typing import Any

from langgraph.errors import GraphBubbleUp

from noesis.runtime.logging import logger


def emit_noesis_event(name: str, payload: dict[str, Any]) -> None:
    """Synchronously emit a named Noesis custom event on both channels."""
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
        try:
            writer(payload)
        except Exception:
            logger.opt(exception=True).debug(
                "stream writer failed for {} event", name,
            )
        from langchain_core.callbacks import dispatch_custom_event

        dispatch_custom_event(name, payload)
    except GraphBubbleUp:
        raise
    except Exception:
        logger.opt(exception=True).debug("Failed to emit {} event", name)


async def aemit_noesis_event(name: str, payload: dict[str, Any]) -> None:
    """Asynchronously emit a named Noesis custom event on both channels."""
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
        try:
            writer(payload)
        except Exception:
            logger.opt(exception=True).debug(
                "stream writer failed for {} event", name,
            )
        from langchain_core.callbacks import adispatch_custom_event

        await adispatch_custom_event(name, payload)
    except GraphBubbleUp:
        raise
    except Exception:
        logger.opt(exception=True).debug("Failed to emit async {} event", name)


__all__ = ["aemit_noesis_event", "emit_noesis_event"]
