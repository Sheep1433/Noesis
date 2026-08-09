"""按用户绑定的跨会话记忆工具。"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.services.memory_dream_service import MemoryDreamService
from noesis.services.user_memory_service import UserMemoryService


class SearchMemoryInput(BaseModel):
    query: str = Field(min_length=1, max_length=100, description="要回忆的关键词或短语")
    date_from: str | None = Field(default=None, description="开始日期 YYYY-MM-DD")
    date_to: str | None = Field(default=None, description="结束日期 YYYY-MM-DD")
    category: str | None = Field(default=None, description="可选分类：fact/decision/preference/todo/problem")
    top_k: int = Field(default=8, ge=1, le=20)


class MemorySourceInput(BaseModel):
    session_id: str
    message_id: str
    context_messages: int = Field(default=1, ge=0, le=3)


def build_memory_tools(
    *,
    user_id: str,
    db: AsyncSession,
    memory_service: Any | None = None,
) -> list[StructuredTool]:
    search_entries = (
        memory_service.search_entries
        if memory_service is not None
        else UserMemoryService.search_entries
    )
    get_source = (
        memory_service.get_source
        if memory_service is not None
        else MemoryDreamService.get_source
    )

    async def search_memory(
        query: str,
        date_from: str | None = None,
        date_to: str | None = None,
        category: str | None = None,
        top_k: int = 8,
    ) -> str:
        items = search_entries(
            user_id,
            query,
            date_from=date_from,
            date_to=date_to,
            category=category,
            limit=top_k,
        )
        return json.dumps({"items": items}, ensure_ascii=False)

    async def get_memory_source(
        session_id: str,
        message_id: str,
        context_messages: int = 1,
    ) -> str:
        try:
            data = await get_source(
                db,
                user_id=user_id,
                session_id=session_id,
                message_id=message_id,
                context_messages=context_messages,
            )
        except LookupError:
            return json.dumps({"error": "记忆来源不存在或无权访问"}, ensure_ascii=False)
        return json.dumps(data, ensure_ascii=False)

    return [
        StructuredTool.from_function(
            coroutine=search_memory,
            name="search_memory",
            description="按需搜索当前用户在其他任务中形成的历史记忆。先搜索摘要，需要核对细节时再读取来源。",
            args_schema=SearchMemoryInput,
        ),
        StructuredTool.from_function(
            coroutine=get_memory_source,
            name="get_memory_source",
            description="读取 search_memory 返回条目的有限原始消息上下文，用于核对记忆来源。",
            args_schema=MemorySourceInput,
        ),
    ]
