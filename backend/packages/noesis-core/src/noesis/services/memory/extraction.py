"""会话终态自动记忆抽取（md-memory-layer tasks 2.x）。

流程：sweep 任务发现 idle 且未抽取的终态会话 → 读会话消息（有界）、
本轮注入清单（run.memory_context）、现有条目 → LLM 五选一判定 →
轻量合并/新建条目 + journal 追加 → 会话标记已抽取。

- 同一用户串行执行（asyncio lock，防并发写覆盖）。
- 崩溃恢复：抽取非事务，失败会话不标记，下次 sweep 补跑。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.config.env import MemoryConfig
from noesis.runtime.logging import logger
from noesis.services.memory.store import MemoryStore
from noesis.services.memory.types import MEMORY_TYPES
from noesis.services.memory.user_settings import MemoryUserSettings
from noesis.storage.postgres.manager import pg_manager
from noesis.storage.postgres.models.chat import TAgentRun, TChatMessage, TChatSession

_USER_LOCKS: dict[str, asyncio.Lock] = {}


def _lock_for(user_id: str) -> asyncio.Lock:
    return _USER_LOCKS.setdefault(user_id, asyncio.Lock())


class ExtractedEntry(BaseModel):
    """LLM 抽取候选。"""

    memory_type: str = Field(description="五选一：preference/goal/decision/experience/gotcha")
    label: str = Field(description="条目标签：中文短语 2-6 字（如「文档格式」「包管理」「学习目标」）")
    slug_hint: str = Field(
        default="",
        description="条目文件名：小写英文短横线（如 document-format）；留空则由系统生成",
    )
    body: str = Field(description="结论正文：陈述句事实，一句话到几句")
    why: str = Field(default="", description="为什么（可选）")
    applicability: str = Field(default="", description="适用条件：何时应用（可选）")
    update_existing_label: str = Field(
        default="",
        description="若为更新/修正既有条目，填该条目的 label；新建留空",
    )
    is_correction: bool = Field(
        default=False,
        description="是否为用户对已注入记忆的修正（修正允许更新注入条目）",
    )


class ExtractionResult(BaseModel):
    entries: list[ExtractedEntry] = Field(default_factory=list)
    journal_summary: str = Field(
        default="",
        description="当日情景摘要（2-4 句，含做了什么、关键结论；无价值会话留空）",
    )


_EXTRACTION_PROMPT = """你是记忆抽取器。从下面的会话中抽取值得长期记住的内容，写入用户记忆库。

## 类型（冻结五类，必须五选一）
- preference 偏好：用户要什么样的输出/行为
- goal 目标：用户现在在做什么（学习计划、进行中的项目）
- decision 决策：定了什么、为什么（选型、方案取舍）
- experience 经验：什么做法有效
- gotcha 注意事项：什么要避开（坑、边界、限制）

## 不该存（负面清单，出现即跳过）
- grep/读文件/代码本身能得出的信息（文件路径、代码结构、git 历史）
- USER.md/AGENTS.md 已写过的内容（见「现有记忆」）
- 临时任务状态、当前对话上下文、寒暄
- 与「本轮已注入条目」相同的内容（除非 is_correction=true，即用户明确修正了它）

## 规则
- 相对日期改写为绝对日期（「下周」→ 具体日期；今天按 {today} 算）
- label 一律中文短语（2-6 字），英文只出现在 slug_hint
- 与「现有记忆」语义重复 → 不新建，设 update_existing_label 为该条目 label
- 明显过时的既有条目被会话推翻 → update_existing_label + 新正文
- 至多 {max_entries} 条新条目；无长期价值 → entries 留空、journal_summary 也留空
- 敏感内容（密钥、凭据、身份证件等）一律不存
- journal_summary 无论如何概述本会话（除非整场无价值）

## 现有记忆
{existing}

## 本轮已注入条目（复述不算新记忆；用户修正 → is_correction）
{injected}

## 会话消息
{messages}"""


class MemoryExtractionService:
    @staticmethod
    async def sweep_once(*, limit: int = 8) -> int:
        """扫描 idle 未抽取会话并抽取；返回处理数。崩溃安全（不标记即重试）。"""
        now_ms = int(time.time() * 1000)
        idle_ms = MemoryConfig.session_idle_minutes * 60 * 1000
        async with pg_manager.get_async_session_context() as db:
            sessions = (
                (
                    await db.execute(
                        select(TChatSession)
                        .where(
                            TChatSession.deleted_at.is_(None),
                            TChatSession.kind == "root",
                            TChatSession.memory_extracted_at.is_(None),
                            TChatSession.updated_at < now_ms - idle_ms,
                            TChatSession.created_at > now_ms - 90 * 24 * 3600 * 1000,
                        )
                        .order_by(TChatSession.updated_at.asc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            candidates = [s for s in sessions if await MemoryExtractionService._has_content(db, s.id)]
        processed = 0
        for session in candidates:
            try:
                async with pg_manager.get_async_session_context() as db:
                    done = await MemoryExtractionService.extract_session(
                        db, session_id=session.id, user_id=str(session.user_id)
                    )
                processed += int(done)
            except Exception:
                logger.warning(
                    "memory extraction failed session_id={}（下次 sweep 重试）",
                    session.id,
                )
        return processed

    @staticmethod
    async def _has_content(db: AsyncSession, session_id: str) -> bool:
        row = (
            await db.execute(
                select(TChatMessage.id)
                .where(TChatMessage.session_id == session_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        return row is not None

    @staticmethod
    async def extract_session(
        db: AsyncSession, *, session_id: str, user_id: str
    ) -> bool:
        """抽取单个会话；同一用户串行。返回是否完成（含禁用/无价值零写入）。"""
        lock = _lock_for(user_id)
        async with lock:
            if not MemoryUserSettings.is_enabled(user_id):
                await MemoryExtractionService._mark_extracted(db, session_id)
                return True
            messages = await MemoryExtractionService._load_messages(db, session_id)
            if not messages:
                await MemoryExtractionService._mark_extracted(db, session_id)
                return True
            injected = await MemoryExtractionService._load_injected(db, session_id)
            MemoryStore.ensure_layout(user_id)
            existing = MemoryStore.read_index(user_id)

            result = await MemoryExtractionService._run_llm(
                user_id=user_id,
                messages=messages,
                injected=injected,
                existing=existing,
            )
            await MemoryExtractionService._apply(
                user_id=user_id,
                session_id=session_id,
                result=result,
                injected=injected,
            )
            await MemoryExtractionService._mark_extracted(db, session_id)
            return True

    # ----- 输入装载 -----

    @staticmethod
    async def _load_messages(db: AsyncSession, session_id: str) -> str:
        rows = (
            (
                await db.execute(
                    select(TChatMessage.role, TChatMessage.content)
                    .where(
                        TChatMessage.session_id == session_id,
                        TChatMessage.status.in_(("completed", "partial")),
                        TChatMessage.role.in_(("user", "assistant")),
                    )
                    .order_by(TChatMessage.message_sequence.asc())
                )
            )
            .all()
        )
        lines: list[str] = []
        budget = MemoryConfig.max_message_chars
        for role, content in rows:
            text = MemoryExtractionService._message_text(content)
            if not text:
                continue
            line = f"[{role}] {text}"
            if sum(len(l) for l in lines) + len(line) > budget:
                lines.append("（其余消息因长度上限截断）")
                break
            lines.append(line)
        return "\n\n".join(lines)

    @staticmethod
    def _message_text(content: object) -> str:
        """multipart JSON 消息提取纯文本（工具输出等非文本段丢弃）。"""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("content", "")))
                elif isinstance(part, str):
                    parts.append(part)
            return "\n".join(p for p in parts if p)
        if isinstance(content, dict):
            return str(content.get("content", ""))
        return ""

    @staticmethod
    async def _load_injected(db: AsyncSession, session_id: str) -> list[str]:
        """聚合本会话各 run 注入清单（run.memory_context['entries']）。"""
        rows = (
            (
                await db.execute(
                    select(TAgentRun.memory_context).where(
                        TAgentRun.session_id == session_id
                    )
                )
            )
            .scalars()
            .all()
        )
        paths: list[str] = []
        for ctx in rows:
            if not ctx:
                continue
            entries = ctx.get("entries") if isinstance(ctx, dict) else None
            if isinstance(entries, list):
                paths.extend(str(item) for item in entries)
        return list(dict.fromkeys(paths))

    # ----- LLM -----

    @staticmethod
    async def _run_llm(
        *, user_id: str, messages: str, injected: list[str], existing
    ) -> ExtractionResult:
        from noesis.llm.factory import get_llm

        existing_lines = [
            f"- [{e.label}] {e.description}（{e.memory_type}/{e.slug}.md）"
            for e in existing.entries
        ] or ["（空）"]
        injected_lines = [f"- {path}" for path in injected] or ["（无）"]
        prompt = _EXTRACTION_PROMPT.format(
            today=datetime.now(timezone.utc).date().isoformat(),
            max_entries=MemoryConfig.max_entries_per_extraction,
            existing="\n".join(existing_lines),
            injected="\n".join(injected_lines),
            messages=messages,
        )
        try:
            llm = get_llm(model_id=MemoryConfig.extraction_model or None)
            structured = llm.with_structured_output(ExtractionResult)
            result = await structured.ainvoke(prompt)
        except Exception as exc:
            logger.warning(
                "memory extraction llm failed user_id={} error={}", user_id, type(exc).__name__
            )
            return ExtractionResult()
        if not isinstance(result, ExtractionResult):
            return ExtractionResult()
        return result

    # ----- 应用 -----

    @staticmethod
    async def _apply(
        *,
        user_id: str,
        session_id: str,
        result: ExtractionResult,
        injected: list[str],
    ) -> None:
        date_str = datetime.now(timezone.utc).date().isoformat()
        source = f"会话 {session_id[:8]} · {date_str}"
        injected_set = set(injected)
        new_count = 0
        for candidate in result.entries:
            if candidate.memory_type not in MEMORY_TYPES:
                continue  # 类型不匹配不入语义层
            target = MemoryStore.slug_of(
                user_id,
                candidate.memory_type,
                candidate.update_existing_label or candidate.label,
            )
            if (
                target
                and f"{candidate.memory_type}/{target}.md" in injected_set
                and not candidate.is_correction
            ):
                continue  # 防自强化：复述注入条目不记录（用户修正除外）
            if target is None:
                if new_count >= MemoryConfig.max_entries_per_extraction:
                    continue  # 超出上限：素材由 journal 覆盖，不建语义条目
                new_count += 1
            MemoryStore.upsert_entry(
                user_id,
                memory_type=candidate.memory_type,
                label=candidate.label,
                body=candidate.body,
                why=candidate.why,
                applicability=candidate.applicability,
                sources=[source],
                slug=target or (candidate.slug_hint or None),
                max_entry_chars=MemoryConfig.max_entry_chars,
            )
        if result.journal_summary.strip():
            MemoryStore.append_journal(
                user_id, session_id=session_id, text=result.journal_summary
            )

    @staticmethod
    async def _mark_extracted(db: AsyncSession, session_id: str) -> None:
        await db.execute(
            update(TChatSession)
            .where(TChatSession.id == session_id)
            .values(memory_extracted_at=int(time.time() * 1000))
        )
        await db.commit()


_SWEEP_TASK: asyncio.Task | None = None


async def start_memory_sweeper() -> None:
    """后台 sweep：idle 会话抽取 + 崩溃补跑（同一循环）；启动 60s 后先扫一次。"""
    global _SWEEP_TASK

    async def _loop() -> None:
        await asyncio.sleep(60)
        while True:
            try:
                processed = await MemoryExtractionService.sweep_once()
                if processed:
                    logger.info("memory sweep extracted sessions={}", processed)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("memory sweep iteration failed")
            await asyncio.sleep(MemoryConfig.sweep_interval_minutes * 60)

    if _SWEEP_TASK is None or _SWEEP_TASK.done():
        _SWEEP_TASK = asyncio.create_task(_loop(), name="memory-extraction-sweeper")


async def stop_memory_sweeper() -> None:
    global _SWEEP_TASK
    if _SWEEP_TASK is not None and not _SWEEP_TASK.done():
        _SWEEP_TASK.cancel()
        try:
            await _SWEEP_TASK
        except asyncio.CancelledError:
            pass
    _SWEEP_TASK = None


__all__ = ["MemoryExtractionService", "start_memory_sweeper", "stop_memory_sweeper"]
