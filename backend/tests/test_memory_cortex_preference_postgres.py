from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import delete

from noesis.services.memory.preferences import MemoryCortexPreferenceService
from noesis.storage.postgres.manager import pg_manager
from noesis.storage.postgres.models.memory import TMemoryUserPreference


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("NOESIS_LIVE_POSTGRES_TEST") != "1",
    reason="设置 NOESIS_LIVE_POSTGRES_TEST=1 后运行真实 PostgreSQL 测试",
)
async def test_single_user_switch_persists_across_sessions() -> None:
    user_id = str(uuid.uuid4())
    try:
        async with pg_manager.get_async_session_context() as db:
            initial = await MemoryCortexPreferenceService.get(db, user_id=user_id)
            assert initial.enabled is False
            enabled = await MemoryCortexPreferenceService.update(
                db, user_id=user_id, enabled=True
            )
            assert enabled.enabled is True

        async with pg_manager.get_async_session_context() as db:
            stored = await MemoryCortexPreferenceService.get(db, user_id=user_id)
            assert stored.enabled is True
            disabled = await MemoryCortexPreferenceService.update(
                db, user_id=user_id, enabled=False
            )
            assert disabled.enabled is False

        async with pg_manager.get_async_session_context() as db:
            stored = await MemoryCortexPreferenceService.get(db, user_id=user_id)
            assert stored.enabled is False
    finally:
        async with pg_manager.get_async_session_context() as db:
            await db.execute(
                delete(TMemoryUserPreference).where(
                    TMemoryUserPreference.user_id == user_id
                )
            )
            await db.commit()
