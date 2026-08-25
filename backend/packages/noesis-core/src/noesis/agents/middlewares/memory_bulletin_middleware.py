"""Freeze one cache-stable Memory Bulletin per root Run and late-insert it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, NotRequired

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
    ResponseT,
)
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from noesis.agents.middlewares.late_context import insert_late_context
from noesis.services.memory.bulletin import MemoryBulletin


class MemoryBulletinState(AgentState):
    memory_bulletin_run_id: NotRequired[Annotated[str, PrivateStateAttr]]
    memory_bulletin_text: NotRequired[Annotated[str, PrivateStateAttr]]
    memory_bulletin_hash: NotRequired[Annotated[str, PrivateStateAttr]]
    memory_bulletin_ids: NotRequired[Annotated[list[str], PrivateStateAttr]]
    memory_bulletin_degraded: NotRequired[Annotated[bool, PrivateStateAttr]]
    memory_bulletin_source_snapshot: NotRequired[Annotated[str, PrivateStateAttr]]


BulletinProvider = Callable[[str], Awaitable[MemoryBulletin]]


def _latest_user_text(state: MemoryBulletinState) -> str:
    for message in reversed(state.get("messages") or []):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


class MemoryBulletinMiddleware(
    AgentMiddleware[MemoryBulletinState, ContextT, ResponseT]
):
    state_schema = MemoryBulletinState

    def __init__(self, *, run_id: str, provider: BulletinProvider):
        self.run_id = run_id
        self.provider = provider

    async def abefore_agent(
        self,
        state: MemoryBulletinState,
        runtime: Runtime[ContextT],  # noqa: ARG002
    ) -> dict | None:
        if state.get("memory_bulletin_run_id") == self.run_id:
            return None
        bulletin = await self.provider(_latest_user_text(state))
        return {
            "memory_bulletin_run_id": self.run_id,
            "memory_bulletin_text": bulletin.text,
            "memory_bulletin_hash": bulletin.bulletin_hash,
            "memory_bulletin_ids": list(bulletin.memory_ids),
            "memory_bulletin_degraded": bulletin.degraded,
            "memory_bulletin_source_snapshot": bulletin.source_snapshot_digest,
        }

    def before_agent(self, state, runtime):  # noqa: ARG002
        if state.get("memory_bulletin_run_id") == self.run_id:
            return None
        raise TypeError("MemoryBulletinMiddleware requires async Agent invocation")

    @staticmethod
    def _with_bulletin(request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
        text = request.state.get("memory_bulletin_text")
        if not text:
            return request
        return request.override(
            messages=insert_late_context(
                list(request.messages), text=text, marker="memory-bulletin"
            )
        )

    def wrap_model_call(self, request, handler):
        return handler(self._with_bulletin(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        return await handler(self._with_bulletin(request))


__all__ = ["BulletinProvider", "MemoryBulletinMiddleware", "MemoryBulletinState"]
