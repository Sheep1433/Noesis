"""Request-scoped context provenance for token source attribution.

Provenance lets capability middleware (Skills, memory, RAG, attachments) record
how many tokens their injected content contributes to the final model request,
so the context breakdown can report ``sources`` (e.g. ``skills``, ``memory``)
without parsing prompt text or inserting debug delimiters.

Design constraints (see openspec add-agent-context-usage-attribution design §2):
- Provenance is request/run scoped and lives in a ContextVar. It SHALL NOT be
  attached to ``ModelRequest`` fields that serialize into the Provider payload.
- The statistics layer (``ContextMetricsMiddleware``) reads provenance when
  building the context snapshot and fills ``sources``; the base ``system`` /
  ``tool_results`` totals are NOT double-counted — a source's tokens are
  reported as a sub-view of the parent category, not added again.
- When a source has no provenance marker, its tokens stay in the parent
  category (``system`` or ``tool_results``); the stats layer SHALL NOT guess.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware.summarization import count_tokens_approximately
from langchain_core.messages import SystemMessage


@dataclass
class ContextProvenance:
    """Accumulated source → estimated token counts for the current request.

    Each entry is a best-effort approximate count of the content a capability
    injected into the final request. The statistics layer subtracts these from
    the parent category only for *display* of ``sources``; ``current_tokens``
    and the breakdown sum are unaffected (no double counting).
    """

    sources: dict[str, int] = field(default_factory=dict)

    def add(self, name: str, tokens: int) -> None:
        """Record (or accumulate) the token cost of a named source."""
        if not name or tokens <= 0:
            return
        self.sources[name] = self.sources.get(name, 0) + tokens

    def snapshot(self) -> dict[str, int]:
        return dict(self.sources)


_CURRENT_PROVENANCE: ContextVar[ContextProvenance | None] = ContextVar(
    "noesis_context_provenance", default=None
)


def current_context_provenance() -> ContextProvenance | None:
    return _CURRENT_PROVENANCE.get()


def get_or_create_context_provenance() -> ContextProvenance:
    """Get the request-scoped provenance, creating it if absent.

    Capability middleware call this in ``before_agent`` / ``before_model`` to
    record their injected content. The ContextVar is request-scoped: it resets
    between requests unless explicitly bound.
    """
    provenance = _CURRENT_PROVENANCE.get()
    if provenance is None:
        provenance = ContextProvenance()
        _CURRENT_PROVENANCE.set(provenance)
    return provenance


def reset_context_provenance() -> None:
    """Clear the request-scoped provenance. Called at request boundaries."""
    _CURRENT_PROVENANCE.set(None)


def estimate_source_tokens(content: Any) -> int:
    """Approximate token count for a piece of injected content.

    Used by capability middleware that has the raw injected value (a system
    prompt section, a skill listing, memory contents) to record provenance
    without re-implementing tokenization. Returns 0 for empty content.
    """
    if content is None:
        return 0
    if isinstance(content, str):
        if not content.strip():
            return 0
        return int(count_tokens_approximately([SystemMessage(content=content)]))
    # Non-string content (dicts, lists, metadata) is stringified for a rough
    # estimate; this is display-only attribution, never billed.
    text = str(content)
    if not text.strip():
        return 0
    return int(count_tokens_approximately([SystemMessage(content=text)]))
