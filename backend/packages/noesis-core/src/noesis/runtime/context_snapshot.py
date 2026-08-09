"""Context snapshot and source models used by Context Lifecycle."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ContextSource:
    name: str
    value: Any
    revision: str | None = None
    rebuildable: bool = True


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    normalized_messages: tuple[Any, ...]
    system_prompt: str | None
    sources: tuple[ContextSource, ...]
    advertised_tools: tuple[Any, ...]
    estimated_tokens: int
    model_limit: int
    provenance: dict[str, Any] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        return {
            "estimated_tokens": self.estimated_tokens,
            "model_limit": self.model_limit,
            "source_names": [item.name for item in self.sources],
            "source_revisions": {item.name: item.revision for item in self.sources if item.revision is not None},
            "advertised_tool_count": len(self.advertised_tools),
        }


_CURRENT_CONTEXT_SNAPSHOT: ContextVar[ContextSnapshot | None] = ContextVar(
    "noesis_context_snapshot", default=None
)


def current_context_snapshot() -> ContextSnapshot | None:
    return _CURRENT_CONTEXT_SNAPSHOT.get()


def set_context_snapshot(value: ContextSnapshot) -> None:
    _CURRENT_CONTEXT_SNAPSHOT.set(value)
