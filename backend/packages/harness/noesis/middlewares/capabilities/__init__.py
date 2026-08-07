"""Profile capability adapters used by Agents."""

from noesis.middlewares.capabilities.turn_memory_middleware import TurnMemoryMiddleware
from noesis.middlewares.capabilities.versioned_skills_middleware import VersionedSkillsMiddleware

__all__ = ["TurnMemoryMiddleware", "VersionedSkillsMiddleware"]
