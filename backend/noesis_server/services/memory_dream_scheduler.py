"""上一自然日 L2 记忆的轻量周期整理器。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, select

from noesis.config.user_data_paths import get_user_daily_memory_path
from noesis.runtime.logging import logger
from noesis_server.infrastructure.database.engine import AsyncSessionLocal
from noesis_server.models.chat_models import TChatSession
from noesis_server.services.memory_dream_service import MemoryDreamService

_POLL_SECONDS = 3600
_TIMEZONE = "Asia/Shanghai"
_task: asyncio.Task | None = None


async def _tick() -> None:
    target_date = (datetime.now(ZoneInfo(_TIMEZONE)).date() - timedelta(days=1)).isoformat()
    async with AsyncSessionLocal() as db:
        user_ids = (await db.execute(select(distinct(TChatSession.user_id)).where(TChatSession.deleted_at.is_(None)))).scalars().all()
        for user_id in user_ids:
            path = get_user_daily_memory_path(user_id, target_date)
            if path.is_file() and f"date={target_date}" in path.read_text(encoding="utf-8", errors="ignore")[:500]:
                continue
            try:
                await MemoryDreamService.run(db, user_id=user_id, target_date=target_date, timezone_name=_TIMEZONE)
            except Exception:
                logger.exception("memory dream failed user_id={} date={}", user_id, target_date)


async def _loop() -> None:
    logger.info("memory dream scheduler started poll={}s", _POLL_SECONDS)
    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("memory dream scheduler tick error")
        await asyncio.sleep(_POLL_SECONDS)


def start_memory_dream_scheduler() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(), name="memory-dream-scheduler")


async def stop_memory_dream_scheduler() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
