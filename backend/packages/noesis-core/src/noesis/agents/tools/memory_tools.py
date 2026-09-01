"""Agent 记忆工具：grep 检索 + 召回清单回写（agent-memory-cortex）。"""

from __future__ import annotations

import json
import time

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.config.env import MemoryConfig
from noesis.runtime.logging import logger
from noesis.services.memory.store import MemoryStore
from noesis.services.memory.types import MEMORY_TYPES
from noesis.storage.postgres.models.chat import TAgentRun


class MemorySearchInput(BaseModel):
    query: str = Field(description="关键词（多个词以空格分隔，任一命中即返回）")
    memory_type: str = Field(
        default="",
        description=f"限定类型（{'/'.join(MEMORY_TYPES)}）；空 = 全部类型",
    )
    limit: int = Field(default=5, ge=1, le=10)


def _stale_warning(mtime: float) -> str:
    age_days = (time.time() - mtime) / 86_400
    if age_days >= MemoryConfig.stale_warning_days:
        return f"（该条目保存于 {int(age_days)} 天前，使用前先验证是否仍然成立）"
    return ""


async def _merge_memory_context(db: AsyncSession, run_id: str, paths: list[str]) -> None:
    """召回清单合并写入 run.memory_context（读-合并-写，去重追加）。

    逐次持久化（write-on-call）：Run 中途崩溃时已召回的条目已入库；
    写失败只记日志不阻断检索结果（防自强化为尽力而为语义）。复用请求级
    session 与旧注入链路同款（消息持久化走独立 session，此处 commit
    只落本次 update）。
    """
    try:
        row = await db.execute(
            select(TAgentRun.memory_context).where(TAgentRun.id == run_id)
        )
        ctx = row.scalar_one_or_none()
        ctx = dict(ctx) if isinstance(ctx, dict) else {}
        entries = ctx.get("entries") if isinstance(ctx.get("entries"), list) else []
        ctx["entries"] = list(dict.fromkeys([*entries, *paths]))
        await db.execute(
            update(TAgentRun)
            .where(TAgentRun.id == run_id)
            .values(memory_context=ctx)
        )
        await db.commit()
    except Exception:
        logger.warning("memory recall writeback failed run_id={}", run_id)
        try:
            await db.rollback()
        except Exception:
            logger.debug("memory recall writeback rollback failed run_id={}", run_id)


def build_memory_tools(
    *,
    user_id: str,
    run_id: str | None = None,
    db: AsyncSession | None = None,
) -> list[StructuredTool]:
    """绑定当前用户的记忆检索工具（grep 语义）。

    root run 传入 run_id 与 db 句柄：命中后合并回写 run.memory_context
    （抽取防自强化输入）；subagent 只读不传，结论经父会话终态回流。
    """

    async def search_memory(query: str, memory_type: str = "", limit: int = 5) -> str:
        types: tuple[str, ...] = ()
        if memory_type:
            if memory_type not in MEMORY_TYPES:
                return json.dumps(
                    {"error": f"非法类型 {memory_type!r}，仅允许 {'/'.join(MEMORY_TYPES)}"},
                    ensure_ascii=False,
                )
            types = (memory_type,)
        try:
            hits = MemoryStore.search(user_id, query, memory_types=types, limit=limit)
        except Exception:
            return json.dumps({"error": "记忆检索暂不可用"}, ensure_ascii=False)
        if hits and run_id and db is not None:
            await _merge_memory_context(
                db, run_id, [str(hit["rel_path"]) for hit in hits]
            )
        if not hits:
            return json.dumps({"results": []}, ensure_ascii=False)
        rendered = []
        for hit in hits:
            path = MemoryStore.entry_path(
                user_id, str(hit["memory_type"]), str(hit["slug"])
            )
            warning = _stale_warning(path.stat().st_mtime) if path.is_file() else ""
            rendered.append({**hit, "stale_warning": warning})
        return json.dumps(
            {"results": rendered},
            ensure_ascii=False,
        )

    tool = StructuredTool.from_function(
        coroutine=search_memory,
        name="search_memory",
        description=(
            "在用户长期记忆（md 文件）中按关键词检索条目原文。"
            "涉及用户偏好、历史决策、既往经验或注意事项时先检索再产出；"
            "结果附条目年龄提示，陈旧条目使用前先验证。"
        ),
        args_schema=MemorySearchInput,
    )
    return [tool]


__all__ = ["build_memory_tools"]
