"""Bind authenticated Run scope to the automatic Bulletin middleware."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.agents.middlewares.memory_bulletin_middleware import MemoryBulletinMiddleware
from noesis.config.env import MachineMemoryConfig
from noesis.services.memory.bulletin import (
    MemoryBulletin,
    MemoryBulletinService,
    render_bulletin,
)
from noesis.services.memory.capture import MemoryCaptureService
from noesis.services.memory.scope import resolve_scope_key
from noesis.runtime.logging import logger


def build_memory_bulletin_middleware(
    *,
    db: AsyncSession | None,
    user_id: str,
    session_id: str,
    run_id: str | None,
    agent_profile: str,
) -> MemoryBulletinMiddleware | None:
    if db is None or not run_id:
        return None
    scope_key = resolve_scope_key(
        user_id=user_id, session_id=session_id, agent_profile=agent_profile
    )

    async def provider(query: str):
        bulletin = await MemoryBulletinService.build(
            db,
            user_id=user_id,
            scope_key=scope_key,
            query=query,
        )
        try:
            await MemoryCaptureService.record_recalled_bulletin(
                run_id=run_id,
                user_id=user_id,
                memory_ids=bulletin.memory_ids,
                bulletin_hash=bulletin.bulletin_hash,
                degraded=bulletin.degraded,
                source_snapshot_digest=bulletin.source_snapshot_digest,
            )
        except Exception:
            logger.warning("failed to persist private memory recall context run_id={}", run_id)
            empty = render_bulletin(
                [], max_tokens=MachineMemoryConfig.bulletin_max_tokens
            )
            return MemoryBulletin(
                text=empty.text,
                bulletin_hash=empty.bulletin_hash,
                memory_ids=empty.memory_ids,
                degraded=True,
            )
        return bulletin

    return MemoryBulletinMiddleware(run_id=run_id, provider=provider)


__all__ = ["build_memory_bulletin_middleware"]
