"""Capability adapters injected by scene agents (skills/memory).

The runtime kernel (RuntimeTelemetry / RunGovernor / ContextLifecycle /
ModelExecution / ToolExecution) has been retired in favour of the
self-contained middleware under ``noesis/middleware/``. This package now only
holds the scene-specific capability adapters that callers pass via
``extra_middleware``.
"""

from noesis.agents.middlewares.capabilities.turn_memory_middleware import TurnMemoryMiddleware
from noesis.agents.middlewares.capabilities.versioned_skills_middleware import VersionedSkillsMiddleware

__all__ = [
    "TurnMemoryMiddleware",
    "VersionedSkillsMiddleware",
]
