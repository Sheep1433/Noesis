"""每 Run 注入选条：小模型从索引选条或全量（md-memory-layer tasks 4.x）。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from noesis.config.env import MemoryConfig
from noesis.runtime.logging import logger
from noesis.services.memory.store import MemoryStore
from noesis.services.memory.types import MEMORY_TYPES


class SelectionResult(BaseModel):
    paths: list[str] = Field(default_factory=list, description="选中的条目相对路径，如 preference/x.md")


_SELECTION_PROMPT = """从记忆索引中为当前问题选出相关条目（top-{top_k}）。

## 索引（每行一条，格式：[标签] 描述 → 路径）
{index}

## 当前问题
{query}

返回选中的条目路径列表；都不相关则返回空列表。"""


class MemorySelectionService:
    @staticmethod
    async def select(
        user_id: str, query: str, *, exclude: set[str] = frozenset(), top_k: int = 5
    ) -> list[str]:
        """返回选中条目的相对路径（type/slug.md）。

        - 记忆量小于注入预算 → 全量（跳过小模型）。
        - 依赖失败 → 空列表（零注入，Run 继续）。
        - exclude = alreadySurfaced：上一 Run 已注入的不重复。
        """
        index = MemoryStore.read_index(user_id)
        entries = [e for e in index.entries if e.rel_path not in exclude]
        if not entries:
            return []
        # 全量判定：索引 + 全部条目正文 < 预算 → 全量（跳过小模型）
        if _all_bodies(user_id, entries) + _index_chars(index) < MemoryConfig.inject_budget_tokens * 4:
            return [e.rel_path for e in entries]
        try:
            selected = await MemorySelectionService._run_llm(
                query=query, entries=entries, top_k=top_k
            )
        except Exception as exc:
            logger.warning("memory selection failed error={}", type(exc).__name__)
            return []
        valid = {e.rel_path for e in entries}
        return [p for p in selected if p in valid][:top_k]

    @staticmethod
    async def _run_llm(*, query: str, entries, top_k: int) -> list[str]:
        from noesis.llm.factory import get_llm

        lines = [
            f"- [{e.label}] {e.description} → {e.rel_path}" for e in entries
        ]
        prompt = _SELECTION_PROMPT.format(
            top_k=top_k,
            index="\n".join(lines),
            query=query or "（无问题，选通用条目）",
        )
        llm = get_llm(model_id=MemoryConfig.selection_model or None)
        result = await llm.with_structured_output(SelectionResult).ainvoke(prompt)
        if not isinstance(result, SelectionResult):
            return []
        return result.paths


def _entry_path(user_id: str, entry) -> Path:
    from noesis.services.memory.store import MemoryStore as _Store

    return _Store.entry_path(user_id, entry.memory_type, entry.slug)


def _all_bodies(user_id: str, entries) -> int:
    total = 0
    for entry in entries:
        path = _entry_path(user_id, entry)
        if path.is_file():
            total += len(path.read_text(encoding="utf-8"))
    return total


def _index_chars(index) -> int:
    return sum(len(e.label) + len(e.description) for e in index.entries)


__all__ = ["MemorySelectionService"]
