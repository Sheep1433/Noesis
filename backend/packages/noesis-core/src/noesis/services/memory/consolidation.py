"""低频记忆整理任务（md-memory-layer tasks 3.x）。

职责：全局去重、矛盾裁决、淘汰（goal 完结检查）、索引压缩。
自动执行无确认；journal 永在，可重建。

触发：固定间隔（consolidation_interval_hours）；索引超预算时立即压缩
（确定性路径，不经 LLM）。
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from pydantic import BaseModel, Field

from noesis.config.env import MemoryConfig
from noesis.runtime.logging import logger
from noesis.services.memory.store import MemoryStore
from noesis.services.memory.types import validate_memory_type
from noesis.storage.postgres.manager import pg_manager
from noesis.storage.postgres.models.auth import TUser

_CONSOLIDATOR_TASK: asyncio.Task | None = None


class ConsolidateAction(BaseModel):
    op: str = Field(description="merge | rewrite | remove | keep")
    target: str = Field(description="目标条目路径 type/slug.md")
    merge_into: str = Field(default="", description="op=merge 时并入的条目路径")
    new_body: str = Field(default="", description="op=rewrite/merge 时的新正文")
    new_label: str = Field(default="", description="op=rewrite/merge 时的新标签（可空）")
    reason: str = Field(default="", description="一句话理由")


class ConsolidationResult(BaseModel):
    actions: list[ConsolidateAction] = Field(default_factory=list)


_CONSOLIDATION_PROMPT = """你是记忆整理器。审查用户记忆条目，产出整理动作。

## 职责
1. 去重：语义重复的条目 → merge（保留信息更全的为目标，另一条并入）
2. 矛盾裁决：新旧冲突按时间与证据取舍 → rewrite（留胜者正文，理由说明）
3. 淘汰：明显过时/低价值（如已完结的目标、失效的临时偏好）→ remove；
   goal 类重点检查：完结的改写为结果性经验（rewrite 到 experience 语义）或淘汰
4. 其余 keep

## 约束
- 只处理「明显」的情况，拿不准一律 keep
- 淘汰不丢信息：journal 保留原始记录
- 输出动作至多 {max_actions} 条

## 条目（type/slug.md · 标签 · 正文）
{entries}

## 索引预算状态
{budget}"""


class MemoryConsolidationService:
    @staticmethod
    async def consolidate_user(user_id: str) -> int:
        """整理单个用户；返回执行的动作数。"""
        MemoryStore.ensure_layout(user_id)
        state = MemoryStore.read_index(user_id)
        if not state.entries:
            return 0
        # 索引超预算：先确定性压缩（死指针删除 + 重建），再继续 LLM 整理
        removed_dead = MemoryConsolidationService._drop_dead_entries(user_id, state)
        state = MemoryStore.read_index(user_id)
        if state.over_budget:
            removed_dead += MemoryConsolidationService._compress_index(user_id)
            state = MemoryStore.read_index(user_id)
        result = await MemoryConsolidationService._run_llm(user_id, state)
        applied = MemoryConsolidationService._apply(user_id, result, removed_dead)
        return applied

    # ----- 确定性维护 -----

    @staticmethod
    def _drop_dead_entries(user_id: str, state) -> int:
        """删除指向不存在文件的索引行（死指针）。"""
        alive = [
            e for e in state.entries if MemoryStore.entry_path(user_id, e.memory_type, e.slug).is_file()
        ]
        if len(alive) != len(state.entries):
            MemoryStore.write_index(user_id, alive)
            return len(state.entries) - len(alive)
        return 0

    @staticmethod
    def _compress_index(user_id: str) -> int:
        """索引超预算：冗余重建（规范化行 + 删死指针）；条目降级由 LLM 动作承担。"""
        before = len(MemoryStore.read_index(user_id).entries)
        MemoryStore.rebuild_index(user_id)
        return before - len(MemoryStore.read_index(user_id).entries)

    # ----- LLM 整理 -----

    @staticmethod
    async def _run_llm(user_id: str, state) -> ConsolidationResult:
        from noesis.llm.factory import get_llm

        lines: list[str] = []
        for entry in state.entries:
            path = MemoryStore.entry_path(user_id, entry.memory_type, entry.slug)
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
            lines.append(f"### {entry.rel_path} · {entry.label}\n{text}\n")
        budget = (
            "索引已超出行数/字节预算，优先产出 merge/remove 减少条目数。"
            if state.over_budget
            else "索引在预算内。"
        )
        prompt = _CONSOLIDATION_PROMPT.format(
            max_actions=12,
            entries="\n".join(lines) or "（空）",
            budget=budget,
        )
        try:
            llm = get_llm(model_id=MemoryConfig.extraction_model or None)
            result = await llm.with_structured_output(ConsolidationResult).ainvoke(prompt)
        except Exception as exc:
            logger.warning("memory consolidation llm failed error={}", type(exc).__name__)
            return ConsolidationResult()
        return result if isinstance(result, ConsolidationResult) else ConsolidationResult()

    @staticmethod
    def _apply(user_id: str, result: ConsolidationResult, base: int) -> int:
        applied = base
        for action in result.actions:
            try:
                applied += MemoryConsolidationService._apply_action(user_id, action)
            except Exception:
                logger.warning("memory consolidation action failed op={} target={}", action.op, action.target)
        return applied

    @staticmethod
    def _apply_action(user_id: str, action: ConsolidateAction) -> int:
        if action.op == "keep":
            return 0
        target = MemoryConsolidationService._parse(user_id, action.target)
        if target is None:
            return 0
        memory_type, slug = target
        if action.op == "remove":
            return int(MemoryStore.remove_entry(user_id, memory_type, slug))
        if action.op == "rewrite":
            existing = MemoryStore.read_entry(user_id, memory_type, slug)
            if existing is None:
                return 0
            MemoryStore.upsert_entry(
                user_id,
                memory_type=memory_type,
                label=action.new_label or str(existing.get("label")),
                body=action.new_body or str(existing.get("body")),
                why=str(existing.get("why") or ""),
                applicability=str(existing.get("applicability") or ""),
                sources=list(existing.get("sources", [])),
                slug=slug,
            )
            return 1
        if action.op == "merge":
            dest = MemoryConsolidationService._parse(user_id, action.merge_into)
            if dest is None or dest == target:
                return 0
            dest_type, dest_slug = dest
            dest_entry = MemoryStore.read_entry(user_id, dest_type, dest_slug)
            if dest_entry is None:
                return 0
            MemoryStore.upsert_entry(
                user_id,
                memory_type=dest_type,
                label=action.new_label or str(dest_entry.get("label")),
                body=action.new_body or str(dest_entry.get("body")),
                why=str(dest_entry.get("why") or ""),
                applicability=str(dest_entry.get("applicability") or ""),
                sources=[
                    *dest_entry.get("sources", []),
                    *MemoryStore.read_entry(user_id, memory_type, slug).get("sources", []),
                ],
                slug=dest_slug,
            )
            MemoryStore.remove_entry(user_id, memory_type, slug)
            return 2
        return 0

    @staticmethod
    def _parse(user_id: str, rel_path: str) -> tuple[str, str] | None:
        try:
            memory_type, slug = rel_path.strip("/").split("/", 1)
            validate_memory_type(memory_type)
            MemoryStore.entry_path(user_id, memory_type, slug.removesuffix(".md"))
            return memory_type, slug.removesuffix(".md")
        except (ValueError, IndexError):
            return None


async def start_memory_consolidator() -> None:
    """低频整理循环：每 interval 小时对全部启用用户跑一次。"""
    global _CONSOLIDATOR_TASK

    async def _loop() -> None:
        interval = MemoryConfig.consolidation_interval_hours * 3600
        while True:
            await asyncio.sleep(interval)
            try:
                async with pg_manager.get_async_session_context() as db:
                    user_ids = (await db.execute(select(TUser.id))).scalars().all()
                for user_id in user_ids:
                    await MemoryConsolidationService.consolidate_user(str(user_id))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("memory consolidation iteration failed")

    if _CONSOLIDATOR_TASK is None or _CONSOLIDATOR_TASK.done():
        _CONSOLIDATOR_TASK = asyncio.create_task(_loop(), name="memory-consolidator")


async def stop_memory_consolidator() -> None:
    global _CONSOLIDATOR_TASK
    if _CONSOLIDATOR_TASK is not None and not _CONSOLIDATOR_TASK.done():
        _CONSOLIDATOR_TASK.cancel()
        try:
            await _CONSOLIDATOR_TASK
        except asyncio.CancelledError:
            pass
    _CONSOLIDATOR_TASK = None


__all__ = ["MemoryConsolidationService", "start_memory_consolidator", "stop_memory_consolidator"]
