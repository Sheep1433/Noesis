"""Important PostgreSQL integration checks for deterministic chat ordering."""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from sqlalchemy import delete, select

from noesis_server.infrastructure.database.engine import AsyncSessionLocal
from noesis_server.models.chat_models import TChatMessage, TChatSession
from noesis.services.chat_service import ChatService


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_writers_allocate_unique_contiguous_sequences() -> None:
    session_id = str(uuid.uuid4())
    now = int(time.time() * 1000)
    async with AsyncSessionLocal() as db:
        db.add(TChatSession(
            id=session_id,
            user_id="1",
            title="sequence-integration",
            next_message_sequence=1,
            created_at=now,
            updated_at=now,
        ))
        await db.commit()

    async def write(content: str) -> None:
        async with AsyncSessionLocal() as db:
            await ChatService.save_message(
                session_id=session_id,
                user_id="1",
                role="user",
                content=content,
                db=db,
            )

    try:
        await asyncio.gather(write("first"), write("second"))
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(TChatMessage)
                    .where(TChatMessage.session_id == session_id)
                    .order_by(TChatMessage.message_sequence.asc())
                )
            ).scalars().all()
            assert [row.message_sequence for row in rows] == [1, 2]
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(TChatMessage).where(TChatMessage.session_id == session_id))
            await db.execute(delete(TChatSession).where(TChatSession.id == session_id))
            await db.commit()
