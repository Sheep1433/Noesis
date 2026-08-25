"""Noesis repositories — domain repositories over ``noesis.storage``.

Knowledge-base collection config + business-domain (agent_run / auth /
settings) repositories. Constructed with an async session; session source is
``noesis.storage.postgres.manager.pg_manager``.
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "AgentRunRepository",
    "KbCollectionConfigRepository",
    "MachineMemoryRepository",
    "MemoryPreferenceRepository",
    "SettingsRepository",
    "SqlAlchemySessionRepository",
    "SqlAlchemyUserRepository",
]


def __getattr__(name: str) -> Any:
    if name == "AgentRunRepository":
        from noesis.repositories.agent_run_repository import AgentRunRepository

        return AgentRunRepository
    if name in {"SqlAlchemySessionRepository", "SqlAlchemyUserRepository"}:
        from noesis.repositories.auth_repository import (
            SqlAlchemySessionRepository,
            SqlAlchemyUserRepository,
        )

        return {
            "SqlAlchemySessionRepository": SqlAlchemySessionRepository,
            "SqlAlchemyUserRepository": SqlAlchemyUserRepository,
        }[name]
    if name == "KbCollectionConfigRepository":
        from noesis.repositories.kb_collection_config_repository import KbCollectionConfigRepository

        return KbCollectionConfigRepository
    if name == "MemoryPreferenceRepository":
        from noesis.repositories.memory_preference_repository import MemoryPreferenceRepository

        return MemoryPreferenceRepository
    if name == "MachineMemoryRepository":
        from noesis.repositories.machine_memory_repository import MachineMemoryRepository

        return MachineMemoryRepository
    if name == "SettingsRepository":
        from noesis.repositories.settings_repository import SettingsRepository

        return SettingsRepository
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
