"""Host-level conversation compaction for human commands."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from langchain.agents import create_agent
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.agents.backends import agent_sandbox_session, create_agent_backend
from noesis.config.checkpointer import get_checkpointer
from noesis.config.code_enum import IntentEnum
from noesis.factory import build_compaction_middleware
from noesis.llm import get_llm
from noesis.repositories.agent_run_repository import AgentRunRepository
from noesis.runtime.logging import logger
from noesis.services.chat_service import ChatService
from noesis.storage.postgres.manager import pg_manager


@dataclass(frozen=True)
class ManualCompactionOutcome:
    """Stable command-facing result; summary text never leaves the host seam."""

    status: str
    pre_message_count: int = 0
    post_message_count: int = 0
    pre_tokens: int = 0
    post_tokens: int = 0


_session_locks: dict[str, asyncio.Lock] = {}


def _session_lock(session_id: str) -> asyncio.Lock:
    lock = _session_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _session_locks[session_id] = lock
    return lock


async def _resolve_model_id(session_id: str, user_id: str, db: AsyncSession) -> str:
    """Reuse the normal QA resolver, including user-defined model snapshots."""
    from noesis.services.qa.helpers import _resolve_model_for_query

    return await _resolve_model_for_query(
        session_id=session_id,
        user_id=user_id,
        request_model_id=None,
        db=db,
    )


@asynccontextmanager
async def _archive_backend(
    profile: str, user_id: str, session_id: str
) -> AsyncIterator[object | None]:
    if profile not in {
        IntentEnum.SUPER_AGENT_QA.value[0],
        IntentEnum.FAULT_OPERATION_QA.value[0],
    }:
        yield None
        return

    async with agent_sandbox_session(user_id, session_id):
        yield await create_agent_backend(user_id, session_id)


async def compact_session(
    *,
    session_id: str,
    user_id: str,
    instructions: str | None = None,
) -> ManualCompactionOutcome:
    """Compact a session checkpoint without creating a user/assistant turn.

    The command is idle-only. The session lock closes the in-process race while
    the database check rejects a run owned by this or another application
    process. The actual summary and checkpoint policy are still owned by
    ``CompactionMiddleware``.
    """
    bounded_instructions = (instructions or "").strip()[:1_000] or None
    async with _session_lock(session_id):
        async with pg_manager.get_async_session_context() as db:
            session = await ChatService.get_session_by_id(session_id, user_id=user_id, db=db)
            if session is None:
                return ManualCompactionOutcome("not_found")
            active = await AgentRunRepository(db).get_active_for_session(user_id, session_id)
            if active is not None:
                return ManualCompactionOutcome("busy")
            session_extra = session.extra if isinstance(session.extra, dict) else {}
            profile = str(session_extra.get("qa_type") or IntentEnum.COMMON_QA.value[0])
            if profile == IntentEnum.TEST_CASE_QA.value[0]:
                return ManualCompactionOutcome("disabled")
            model_id = await _resolve_model_id(session_id, user_id, db)

        async with _archive_backend(profile, user_id, session_id) as backend:
            middleware = build_compaction_middleware(model_id=model_id, backend=backend)
            if middleware is None:
                return ManualCompactionOutcome("disabled")

            checkpointer = get_checkpointer()
            config = {"configurable": {"thread_id": session_id}}
            # This graph is only the checkpoint adapter. No model call or tool
            # execution is performed; the compaction middleware is the single
            # owner of summary, archive, boundary and policy construction.
            graph = create_agent(
                model=get_llm(model_id=model_id),
                tools=[],
                system_prompt="",
                middleware=[middleware],
                checkpointer=checkpointer,
            )
            snapshot = await graph.aget_state(config)

            async def checkpoint(update: dict[str, object]) -> None:
                await graph.aupdate_state(config, update, as_node="model")

            compacted = await middleware.acompact_state(
                snapshot.values,
                session_id,
                instructions=bounded_instructions,
                checkpoint=checkpoint,
            )
            if compacted is None:
                return ManualCompactionOutcome("no_history")
            logger.info(
                "manual compaction completed session_id={} user_id={} mode=manual "
                "messages={}→{} tokens={}→{}",
                session_id,
                user_id,
                compacted.pre_message_count,
                compacted.post_message_count,
                compacted.pre_tokens,
                compacted.post_tokens,
            )
            return ManualCompactionOutcome(
                "completed",
                pre_message_count=compacted.pre_message_count,
                post_message_count=compacted.post_message_count,
                pre_tokens=compacted.pre_tokens,
                post_tokens=compacted.post_tokens,
            )


__all__ = ["ManualCompactionOutcome", "compact_session"]
