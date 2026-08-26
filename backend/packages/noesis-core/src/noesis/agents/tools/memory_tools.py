"""Agent 记忆工具：grep 检索（md-memory-layer task 5.1）。"""

from __future__ import annotations

import json

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from noesis.services.memory.store import MemoryStore
from noesis.services.memory.types import MEMORY_TYPES


class MemorySearchInput(BaseModel):
    query: str = Field(description="关键词（多个词以空格分隔，任一命中即返回）")
    memory_type: str = Field(
        default="",
        description=f"限定类型（{'/'.join(MEMORY_TYPES)}）；空 = 全部类型",
    )
    limit: int = Field(default=5, ge=1, le=10)


def build_memory_tools(*, user_id: str) -> list[StructuredTool]:
    """绑定当前用户的只读记忆检索工具（grep 语义，无 DB 依赖）。"""

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
        if not hits:
            return json.dumps({"results": []}, ensure_ascii=False)
        return json.dumps(
            {"results": hits},
            ensure_ascii=False,
        )

    tool = StructuredTool.from_function(
        coroutine=search_memory,
        name="search_memory",
        description=(
            "在用户长期记忆（md 文件）中按关键词检索条目原文。"
            "适用于需要回忆用户偏好、历史决策、既往经验或注意事项的场景。"
        ),
        args_schema=MemorySearchInput,
    )
    return [tool]


__all__ = ["build_memory_tools"]
