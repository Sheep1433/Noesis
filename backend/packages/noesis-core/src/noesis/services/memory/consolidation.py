"""低频记忆整理任务（agent-memory-cortex）——AutoDream 形态。

职责：全局去重、矛盾裁决、淘汰（goal 完结检查）、索引压缩，并把近期
journal 情景信号（反复主题、用户纠正）纳入整理依据。自动执行无确认；
journal 永在（含改前快照），可重建。

可观测与治理：
- 改写/合并/淘汰前把改前条目全文（含 frontmatter）快照追加进当日
  journal——「从情景层重建条目」为机械操作；
- 矛盾裁决三级优先：用户显式修正 > 稳定类型 > 时间与证据；
- frontmatter type 与所在目录不一致的条目机械归位（位置即用户意图）；
- 整理的情景信号输入排除整理快照块（块头标记识别，含内嵌标题的快照全文）。

触发门控（对齐 Claude Code AutoDream）：距上次整理超过
consolidation_min_interval_hours 且期间新抽取会话数 ≥
consolidation_min_new_sessions，两条件同时满足才跑（无活动日不空转）。
门控检查挂在抽取 sweep 循环尾部，每 sweep_interval_minutes 顺带评估。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from noesis.config.env import MemoryConfig
from noesis.runtime.logging import logger
from noesis.services.memory.store import MemoryStore
from noesis.services.memory.types import MEMORY_TYPES, validate_memory_type
from noesis.storage.postgres.manager import pg_manager
from noesis.storage.postgres.models.chat import TChatSession

_CONSOLIDATOR_TASK: asyncio.Task | None = None

_STATE_FILE = ".consolidation_state.json"
_JOURNAL_INPUT_DAYS = 7
_JOURNAL_MAX_CHARS = 8_000
_SNAPSHOT_MARKER = "整理快照"
# journal 块头格式（append_journal 生成）：## HH:MM[ · 会话 xxxx][（label）]
_JOURNAL_BLOCK_HEADER_RE = re.compile(r"^## \d{1,2}:\d{2}")


class ConsolidateAction(BaseModel):
    op: str = Field(description="merge | rewrite | remove | keep")
    target: str = Field(description="目标条目路径 type/slug.md")
    merge_into: str = Field(default="", description="op=merge 时并入的条目路径")
    new_body: str = Field(default="", description="op=rewrite/merge 时的新正文")
    new_label: str = Field(default="", description="op=rewrite/merge 时的新标签（可空）")
    new_description: str = Field(
        default="",
        description="op=rewrite/merge 时的新 description（两段式：是什么；何时调用）；留空保留原值（merge 以保留方为准）",
    )
    reason: str = Field(default="", description="一句话理由")


class ConsolidationResult(BaseModel):
    actions: list[ConsolidateAction] = Field(default_factory=list)


_CONSOLIDATION_PROMPT = """你是记忆整理器。审查用户记忆条目与近期情景日志，产出整理动作。

## 职责
1. 去重：语义重复的条目 → merge（保留信息更全的为目标，另一条并入）
2. 矛盾裁决 → rewrite（留胜者正文，理由说明）。裁决按三级优先：
   ① 用户显式修正最高：近期日志中有用户明确推翻旧说法的记录时，以修正为准
   ② 稳定类型 > 动态类型：goal 类新内容不得静默改写 preference/decision/
      experience/gotcha 条目——确实冲突时改写动态侧或产出新条目，
      稳定侧条目仅在显式修正证据下改写
   ③ 同级才按时间与证据取舍
3. 淘汰：明显过时/低价值（如已完结的目标、失效的临时偏好）→ remove；
   goal 类重点检查：完结的改写为结果性经验（rewrite 到 experience 语义）或淘汰
4. 情景信号：近期日志中反复出现的主题、用户多次纠正的点，若现有条目
   表述不准或缺失 → rewrite 补强；信号不足以立新条目的不要凭空新建
5. 其余 keep

## 约束
- 只处理「明显」的情况，拿不准一律 keep
- 淘汰不丢信息：journal 保留原始记录与改前快照
- rewrite/merge 换了正文时必须同步给 new_description（新正文的
  「一句话结论；何时调用」）；正文未变则留空保留原值
- 输出动作至多 {max_actions} 条

## 条目（type/slug.md · 标签 · 正文）
{entries}

## 近期情景日志（只作信号参考，不逐条处理）
{journal}

## 索引预算状态
{budget}"""


def _state_path(user_id: str) -> Path:
    return MemoryStore.memory_root(user_id) / _STATE_FILE


def _read_state(user_id: str) -> dict:
    path = _state_path(user_id)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_state(user_id: str, **values) -> None:
    state = _read_state(user_id)
    state.update(values)
    MemoryStore.ensure_layout(user_id)
    _state_path(user_id).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class MemoryConsolidationService:
    @staticmethod
    async def should_consolidate(user_id: str) -> tuple[bool, dict]:
        """AutoDream 门控：间隔 + 期间新抽取会话数双条件。"""
        state = _read_state(user_id)
        last_run = float(state.get("last_run_ms") or 0)
        interval_ms = MemoryConfig.consolidation_min_interval_hours * 3600 * 1000
        if time.time() * 1000 - last_run < interval_ms:
            return False, state
        async with pg_manager.get_async_session_context() as db:
            since = int(last_run)
            new_sessions = (
                await db.execute(
                    select(func.count())
                    .select_from(TChatSession)
                    .where(
                        TChatSession.user_id == user_id,
                        TChatSession.memory_extracted_at.isnot(None),
                        TChatSession.memory_extracted_at > since,
                    )
                )
            ).scalar() or 0
        if new_sessions < MemoryConfig.consolidation_min_new_sessions:
            return False, state
        return True, state

    @staticmethod
    async def consolidate_user(user_id: str) -> int:
        """整理单个用户；返回执行的动作数。"""
        MemoryStore.ensure_layout(user_id)
        # frontmatter type 与目录不一致的机械归位（位置即用户意图）；
        # 先于索引判空执行——索引被清空但条目文件仍在时同样需要归位
        aligned = MemoryConsolidationService._align_frontmatter_types(user_id)
        state = MemoryStore.read_index(user_id)
        if not state.entries:
            _write_state(user_id, last_run_ms=int(time.time() * 1000))
            return len(aligned)
        # 索引超预算：先确定性压缩（死指针删除 + 重建），再继续 LLM 整理
        removed_dead = MemoryConsolidationService._drop_dead_entries(user_id, state)
        state = MemoryStore.read_index(user_id)
        if state.over_budget:
            removed_dead += MemoryConsolidationService._compress_index(user_id)
            state = MemoryStore.read_index(user_id)
        result = await MemoryConsolidationService._run_llm(user_id, state)
        applied = MemoryConsolidationService._apply(user_id, result, removed_dead + len(aligned))
        _write_state(user_id, last_run_ms=int(time.time() * 1000))
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

    @staticmethod
    def _align_frontmatter_types(user_id: str) -> list[str]:
        """frontmatter type 与所在目录不一致 → 以目录为准归位（不移动文件）。

        引擎写入路径自身保证一致；不一致几乎总源于用户挪动文件。归位
        动作记录进 journal。
        """
        fixes: list[str] = []
        for memory_type in MEMORY_TYPES:
            directory = MemoryStore.memory_root(user_id) / memory_type
            for path in sorted(directory.glob("*.md")):
                old_type = MemoryStore.align_frontmatter_type(
                    user_id, memory_type, path.stem
                )
                if old_type is not None:
                    fixes.append(
                        f"{memory_type}/{path.stem}.md（frontmatter {old_type} → {memory_type}）"
                    )
        if fixes:
            MemoryStore.append_journal(
                user_id,
                session_id=None,
                text="frontmatter 类型与所在目录不一致，已按目录归位：\n"
                + "\n".join(f"- {fix}" for fix in fixes),
                label="整理归位",
            )
        return fixes

    # ----- LLM 整理 -----

    @staticmethod
    def _strip_snapshot_blocks(text: str) -> str:
        """剔除整理快照块（块头含「整理快照」标记）。

        块头按 ``## HH:MM`` 时间戳模式识别（append_journal 的生成格式）：
        快照正文若内嵌普通 ``## `` 行，不会解除跳过状态，保证快照全文
        不进入情景信号输入。
        """
        kept: list[str] = []
        skipping = False
        for line in text.splitlines():
            if _JOURNAL_BLOCK_HEADER_RE.match(line):
                skipping = _SNAPSHOT_MARKER in line
                if skipping:
                    continue
            if not skipping:
                kept.append(line)
        return "\n".join(kept).strip()

    @staticmethod
    def _recent_journal(user_id: str) -> str:
        """近 N 天 journal 内容（有界，剔除整理快照块），作为 AutoDream Gather 阶段的信号源。"""
        root = MemoryStore.memory_root(user_id) / "journal"
        if not root.is_dir():
            return "（无）"
        cutoff = datetime.now().astimezone().date() - timedelta(days=_JOURNAL_INPUT_DAYS)
        chunks: list[str] = []
        total = 0
        for path in sorted(root.glob("*.md"), reverse=True):
            try:
                day = datetime.strptime(path.stem, "%Y-%m-%d").date()
            except ValueError:
                continue
            if day < cutoff:
                continue
            text = MemoryConsolidationService._strip_snapshot_blocks(
                path.read_text(encoding="utf-8").strip()
            )
            if not text:
                continue
            if total + len(text) > _JOURNAL_MAX_CHARS:
                remaining = _JOURNAL_MAX_CHARS - total
                if remaining > 200:
                    chunks.append(f"### {path.stem}\n{text[:remaining]}…")
                break
            chunks.append(f"### {path.stem}\n{text}")
            total += len(text)
        return "\n\n".join(chunks) or "（无）"

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
            journal=MemoryConsolidationService._recent_journal(user_id),
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
    def _snapshot_entry(user_id: str, memory_type: str, slug: str) -> bool:
        """改前条目全文（含 frontmatter）快照追加进当日 journal。"""
        path = MemoryStore.entry_path(user_id, memory_type, slug)
        if not path.is_file():
            return False
        MemoryStore.append_journal(
            user_id,
            session_id=None,
            text=path.read_text(encoding="utf-8"),
            label=f"整理快照 · 原条目 {memory_type}/{slug}.md",
        )
        return True

    @staticmethod
    def _apply_action(user_id: str, action: ConsolidateAction) -> int:
        if action.op == "keep":
            return 0
        target = MemoryConsolidationService._parse(user_id, action.target)
        if target is None:
            return 0
        memory_type, slug = target
        if action.op == "remove":
            if not MemoryConsolidationService._snapshot_entry(user_id, memory_type, slug):
                return 0
            return int(MemoryStore.remove_entry(user_id, memory_type, slug))
        if action.op == "rewrite":
            existing = MemoryStore.read_entry(user_id, memory_type, slug)
            if existing is None:
                return 0
            MemoryConsolidationService._snapshot_entry(user_id, memory_type, slug)
            MemoryStore.upsert_entry(
                user_id,
                memory_type=memory_type,
                label=action.new_label or str(existing.get("label")),
                body=action.new_body or str(existing.get("body")),
                why=str(existing.get("why") or ""),
                applicability=str(existing.get("applicability") or ""),
                description=action.new_description,
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
            merged = MemoryStore.read_entry(user_id, memory_type, slug)
            MemoryConsolidationService._snapshot_entry(user_id, memory_type, slug)
            MemoryConsolidationService._snapshot_entry(user_id, dest_type, dest_slug)
            MemoryStore.upsert_entry(
                user_id,
                memory_type=dest_type,
                label=action.new_label or str(dest_entry.get("label")),
                body=action.new_body or str(dest_entry.get("body")),
                why=str(dest_entry.get("why") or ""),
                applicability=str(dest_entry.get("applicability") or ""),
                # merge 以保留方（dest）的 description 为准：new_description 为空时
                # upsert 合并语义保留 dest 既有值
                description=action.new_description,
                sources=[
                    *dest_entry.get("sources", []),
                    *(merged or {}).get("sources", []),
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


async def maybe_consolidate() -> int:
    """对全部启用用户跑门控评估，通过则整理；返回整理的用户数。"""
    from noesis.services.memory.user_settings import MemoryUserSettings

    consolidated = 0
    try:
        async with pg_manager.get_async_session_context() as db:
            user_ids = (await db.execute(select(TChatSession.user_id).distinct())).scalars().all()
        for user_id in user_ids:
            uid = str(user_id)
            if not MemoryUserSettings.is_enabled(uid):
                continue
            should, _state = await MemoryConsolidationService.should_consolidate(uid)
            if not should:
                continue
            applied = await MemoryConsolidationService.consolidate_user(uid)
            consolidated += 1
            logger.info("memory consolidation done user_id={} actions={}", uid, applied)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("memory consolidation iteration failed")
    return consolidated


async def start_memory_consolidator() -> None:
    """门控挂在 sweep 循环尾部，本函数仅为兼容保留（见 extraction sweeper）。"""
    # 整理由 extraction sweeper 每 sweep_interval_minutes 调 maybe_consolidate；
    # 独立循环已移除，避免双循环重复触发。
    return


async def stop_memory_consolidator() -> None:
    global _CONSOLIDATOR_TASK
    if _CONSOLIDATOR_TASK is not None and not _CONSOLIDATOR_TASK.done():
        _CONSOLIDATOR_TASK.cancel()
        try:
            await _CONSOLIDATOR_TASK
        except asyncio.CancelledError:
            pass
    _CONSOLIDATOR_TASK = None


__all__ = [
    "MemoryConsolidationService",
    "maybe_consolidate",
    "start_memory_consolidator",
    "stop_memory_consolidator",
]
