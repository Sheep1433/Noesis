"""Source refresh middleware — source revision + targeted cache invalidation.

Computes a source revision per top-level user turn for the stable context
sources (skills, memory, tool catalog, attachments, scene prompt) and
invalidates the corresponding upstream private cache when it changes. This is
the Noesis owner for Claude-Code-style source freshness; DeepAgents
``SkillsMiddleware`` / ``MemoryMiddleware`` load **once per session**
(``if "skills_metadata" in state: return None`` — see ``skills.py:959``) and
have no invalidation hook, so user-installed skills never reload.

Design contract (``simplify-agent-context-architecture`` §5):

- compute a source fingerprint per top-level turn for skills / memory / tool
  catalog / attachments / scene prompt;
- when revision is unchanged, keep state and prompt prefix stable;
- when revision changes, clear only the affected upstream private cache so
  Skills/Memory reload at this turn's start;
- within one run the revision is fixed — a tool writing memory mid-run must
  not change the prompt on the next model call;
- compact, subagent fork and resume carry an explicit revision.

Upstream limitation: ``SkillsMiddleware`` reloads only when the
``skills_metadata`` key is *absent* from state (membership check, not
truthiness). SourceRefresh therefore ``pop``s the upstream keys
(``skills_metadata``, ``skills_load_errors``, ``memory_contents``) from the
threaded state when a revision change is detected. This relies on LangChain
middleware running outer ``before_agent`` handlers before inner ones on the
threaded state dict; checkpoint-level key deletion is not guaranteed by
LangGraph default channels, so the invalidation is a within-turn signal.

Self-containment: sources are fingerprinted via an injected ``source_provider``;
no ``runtime``/``service`` calls.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ResponseT,
)
from langgraph.runtime import Runtime

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# Upstream private-state keys that gate the load-once behaviour.
SKILLS_CACHE_KEYS: tuple[str, ...] = ("skills_metadata", "skills_load_errors")
MEMORY_CACHE_KEYS: tuple[str, ...] = ("memory_contents",)
ALL_CACHE_KEYS: tuple[str, ...] = SKILLS_CACHE_KEYS + MEMORY_CACHE_KEYS


@dataclass(frozen=True)
class SourceFingerprint:
    """A fingerprint of the stable context sources for one turn."""

    skills_hash: str | None = None
    memory_hash: str | None = None
    tool_catalog_hash: str | None = None
    attachments_hash: str | None = None
    scene_prompt_hash: str | None = None

    def revision(self) -> str:
        payload = "|".join(
            h or "-"
            for h in (
                self.skills_hash,
                self.memory_hash,
                self.tool_catalog_hash,
                self.attachments_hash,
                self.scene_prompt_hash,
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


SourceFingerprintProvider = Callable[[], "SourceFingerprint | None"]
"""Returns the current source fingerprint. Injected by the factory as a closure
over the resolved skills dirs / memory files / tool catalog / attachments."""


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


@dataclass
class _RevisionState:
    """Private state for the current run's source revision."""

    revision: str | None = None
    fingerprint: SourceFingerprint | None = None
    invalidated_keys: list[str] = field(default_factory=list)


def _changed_sources(prev: SourceFingerprint, curr: SourceFingerprint) -> list[str]:
    changed: list[str] = []
    if prev.skills_hash != curr.skills_hash:
        changed.append("skills")
    if prev.memory_hash != curr.memory_hash:
        changed.append("memory")
    return changed


class SourceRefreshMiddleware(AgentMiddleware[AgentState[ResponseT], ContextT, ResponseT]):
    """Compute source revision and invalidate upstream caches on change."""

    def __init__(
        self,
        source_provider: SourceFingerprintProvider | None = None,
        *,
        cache_keys: tuple[str, ...] = ALL_CACHE_KEYS,
    ) -> None:
        self._source_provider = source_provider
        self._cache_keys = cache_keys

    @staticmethod
    def _state(state: AgentState[Any]) -> _RevisionState:
        raw = state.get("_source_revision")
        if isinstance(raw, _RevisionState):
            return raw
        if isinstance(raw, dict):
            fp_data = raw.get("fingerprint") or {}
            fp = SourceFingerprint(**{k: fp_data.get(k) for k in (
                "skills_hash", "memory_hash", "tool_catalog_hash", "attachments_hash", "scene_prompt_hash",
            )}) if fp_data else None
            return _RevisionState(revision=raw.get("revision"), fingerprint=fp, invalidated_keys=list(raw.get("invalidated_keys") or []))
        return _RevisionState()

    def _current_fingerprint(self) -> SourceFingerprint | None:
        if self._source_provider is None:
            return None
        return self._source_provider()

    def _keys_to_invalidate(self, prev: SourceFingerprint | None, curr: SourceFingerprint) -> tuple[str, ...]:
        if prev is None:
            return self._cache_keys  # first turn: ensure (re)load
        changed = _changed_sources(prev, curr)
        keys: list[str] = []
        if "skills" in changed:
            keys.extend(k for k in self._cache_keys if k in SKILLS_CACHE_KEYS)
        if "memory" in changed:
            keys.extend(k for k in self._cache_keys if k in MEMORY_CACHE_KEYS)
        if not keys and curr.revision() != (prev.revision() if prev else None):
            # tool catalog / attachments / scene prompt changed → reload skills+memory
            # is NOT implied; only clear caches tied to changed sources.
            keys = []
        return tuple(dict.fromkeys(keys))

    def invalidate(self, state: AgentState[Any], keys: tuple[str, ...]) -> list[str]:
        """Remove the given upstream cache keys from the threaded state dict.

        Returns the keys actually removed (for observability). This is a
        within-turn signal: the next ``before_agent`` handler (Skills/Memory)
        sees the key absent and reloads.
        """
        removed: list[str] = []
        for key in keys:
            if key in state:
                state.pop(key, None)  # type: ignore[misc]
                removed.append(key)
        return removed

    def before_agent(
        self,
        state: AgentState[Any],
        runtime: Runtime[ContextT],  # noqa: ARG002
    ) -> dict[str, Any] | None:
        rev_state = self._state(state)
        curr = self._current_fingerprint()
        if curr is None:
            return None
        new_revision = curr.revision()
        # Within-run stability: if a revision is already fixed for this run and
        # the fingerprint is unchanged, do nothing.
        if rev_state.revision == new_revision and rev_state.fingerprint == curr:
            return None
        keys = self._keys_to_invalidate(rev_state.fingerprint, curr)
        removed = self.invalidate(state, keys)
        new_state = _RevisionState(
            revision=new_revision,
            fingerprint=curr,
            invalidated_keys=removed,
        )
        # Persist as a plain dict so it survives checkpoint serialisation.
        fp = new_state.fingerprint
        return {
            "_source_revision": {
                "revision": new_state.revision,
                "fingerprint": {
                    "skills_hash": fp.skills_hash if fp else None,
                    "memory_hash": fp.memory_hash if fp else None,
                    "tool_catalog_hash": fp.tool_catalog_hash if fp else None,
                    "attachments_hash": fp.attachments_hash if fp else None,
                    "scene_prompt_hash": fp.scene_prompt_hash if fp else None,
                },
                "invalidated_keys": list(new_state.invalidated_keys),
            },
        }


__all__ = [
    "ALL_CACHE_KEYS",
    "MEMORY_CACHE_KEYS",
    "SKILLS_CACHE_KEYS",
    "SourceFingerprint",
    "SourceFingerprintProvider",
    "SourceRefreshMiddleware",
]
