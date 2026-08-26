"""每 Run 注入选中的记忆条目正文（Run 级冻结快照 + alreadySurfaced）。

通道纪律（md-memory-layer design §5）：
- USER.md + MEMORY.md 索引在稳定前缀（RefreshingMemoryMiddleware，会话内不变）；
- 本中间件只注入每 Run 变化的选中条目正文，走 late-context 追加通道——
  cache 代价有界（每 Run 首调用重算上一轮），且不触碰稳定前缀区。
"""

from __future__ import annotations

import time
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
from noesis.config.env import MemoryConfig
from noesis.runtime.logging import logger
from noesis.services.memory.selection import MemorySelectionService
from noesis.services.memory.store import MemoryStore

_BLOCK_HEADER = "以下为历史经验记忆，可能过时；与用户当前要求冲突时以用户为准。"


class MemoryEntriesState(AgentState):
    memory_entries_run_id: NotRequired[Annotated[str, PrivateStateAttr]]
    memory_entries_text: NotRequired[Annotated[str, PrivateStateAttr]]
    memory_entries_paths: NotRequired[Annotated[list[str], PrivateStateAttr]]
    memory_entries_surfaced: NotRequired[Annotated[list[str], PrivateStateAttr]]


def _latest_user_text(state: MemoryEntriesState) -> str:
    for message in reversed(state.get("messages") or []):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def _stale_warning(mtime: float) -> str:
    age_days = (time.time() - mtime) / 86_400
    if age_days >= MemoryConfig.stale_warning_days:
        return f"（该条目保存于 {int(age_days)} 天前，使用前先验证是否仍然成立）"
    return ""


class MemoryEntriesMiddleware(
    AgentMiddleware[MemoryEntriesState, ContextT, ResponseT]
):
    """Run 级冻结：同一 run_id（含 tool loop 与 HITL resume）注入相同快照。"""

    state_schema = MemoryEntriesState

    def __init__(
        self,
        *,
        run_id: str,
        user_id: str,
        select: Callable[[str, frozenset[str]], Awaitable[list[str]]],
    ):
        self.run_id = run_id
        self.user_id = user_id
        self._select = select

    async def abefore_agent(
        self,
        state: MemoryEntriesState,
        runtime: Runtime[ContextT],  # noqa: ARG002
    ) -> dict | None:
        if state.get("memory_entries_run_id") == self.run_id:
            return None
        surfaced = frozenset(state.get("memory_entries_surfaced") or [])
        paths: list[str] = []
        try:
            paths = await self._select(_latest_user_text(state), surfaced)
        except Exception as exc:
            logger.warning(
                "memory entries selection failed user_id={} error={}",
                self.user_id,
                type(exc).__name__,
            )
            paths = []
        text = self._render(paths)
        return {
            "memory_entries_run_id": self.run_id,
            "memory_entries_text": text,
            "memory_entries_paths": paths,
            # alreadySurfaced：本 Run 注入的条目下一 Run 不重复
            "memory_entries_surfaced": sorted(set(surfaced) | set(paths)),
        }

    def _render(self, paths: list[str]) -> str:
        if not paths:
            return ""
        sections: list[str] = [_BLOCK_HEADER]
        for rel_path in paths:
            memory_type, slug = rel_path.removesuffix(".md").split("/", 1)
            entry = MemoryStore.read_entry(self.user_id, memory_type, slug)
            if not entry:
                continue
            path = MemoryStore.entry_path(self.user_id, memory_type, slug)
            warning = _stale_warning(path.stat().st_mtime) if path.is_file() else ""
            sections.append(f"### {entry.get('label')} {warning}\n\n{entry.get('body')}")
        return "\n\n".join(sections) if len(sections) > 1 else ""

    @staticmethod
    def _with_context(request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
        text = request.state.get("memory_entries_text")
        if not text:
            return request
        return request.override(
            messages=insert_late_context(
                list(request.messages), text=text, marker="memory-entries"
            ),
        )

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        return handler(self._with_context(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelRequest[ContextT]]],
    ) -> ModelResponse[ResponseT]:
        return await handler(self._with_context(request))


async def build_memory_entries_middleware(
    *,
    db,
    user_id: str,
    session_id: str,  # noqa: ARG001
    run_id: str | None,
) -> MemoryEntriesMiddleware | None:
    """装配：选条 + 注入清单回写 run.memory_context（防自强化输入）。"""
    if db is None or not run_id:
        return None

    async def select(query: str, exclude: frozenset[str]) -> list[str]:
        from noesis.services.memory.user_settings import MemoryUserSettings

        if not MemoryUserSettings.is_enabled(user_id):
            return []
        paths = await MemorySelectionService.select(user_id, query, exclude=exclude)
        if paths:
            await _persist_memory_context(db, run_id, paths)
        return paths

    return MemoryEntriesMiddleware(run_id=run_id, user_id=user_id, select=select)


async def _persist_memory_context(db, run_id: str, paths: list[str]) -> None:
    """注入清单回写 run.memory_context（抽取防自强化的输入）。"""
    from sqlalchemy import update

    from noesis.storage.postgres.models.chat import TAgentRun

    try:
        await db.execute(
            update(TAgentRun)
            .where(TAgentRun.id == run_id)
            .values(memory_context={"entries": paths})
        )
        await db.commit()
    except Exception:
        logger.warning("memory context writeback failed run_id={}", run_id)
        await db.rollback()


__all__ = ["MemoryEntriesMiddleware", "build_memory_entries_middleware"]
