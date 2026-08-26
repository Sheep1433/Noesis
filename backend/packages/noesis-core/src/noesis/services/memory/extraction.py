"""会话终态自动记忆抽取（md-memory-layer tasks 2.x）——水位增量。

流程：sweep 发现 idle 且有新消息越过水位的 root 会话 → 读取
水位之后的新消息段（带水位前 2 条衔接背景，有界截断保头保尾）、
本轮注入清单（run.memory_context）、现有条目 → LLM 五选一判定 →
轻量合并/新建条目 + journal 追加 → 推进水位。

- 水位（memory_extracted_seq）= 已成功抽取的最大消息序号；
  成功才推进，失败保留原水位（Claude Code cursor 同款语义）。
- 同一用户串行执行（asyncio lock，防并发写覆盖）。
- subagent 会话（kind='subagent'）不抽取：结论经父会话终态通知回流。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
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
        """扫描 idle 且有新消息越过水位的 root 会话并抽取；返回处理数。

        崩溃安全：抽取失败不推进水位，下次 sweep 重试同一段。
        """
        now_ms = int(time.time() * 1000)
        idle_ms = MemoryConfig.session_idle_minutes * 60 * 1000
        eligible_max = (
            select(
                TChatMessage.session_id.label("sid"),
                func.max(TChatMessage.message_sequence).label("max_seq"),
            )
            .where(
                TChatMessage.status.in_(("completed", "partial")),
                TChatMessage.role.in_(("user", "assistant")),
            )
            .group_by(TChatMessage.session_id)
            .subquery()
        )
        async with pg_manager.get_async_session_context() as db:
            candidates = (
                (
                    await db.execute(
                        select(TChatSession, eligible_max.c.max_seq)
                        .join(eligible_max, eligible_max.c.sid == TChatSession.id)
                        .where(
                            TChatSession.deleted_at.is_(None),
                            TChatSession.kind == "root",
                            TChatSession.updated_at < now_ms - idle_ms,
                            TChatSession.created_at > now_ms - 90 * 24 * 3600 * 1000,
                            or_(
                                TChatSession.memory_extracted_seq.is_(None),
                                eligible_max.c.max_seq > TChatSession.memory_extracted_seq,
                            ),
                        )
                        .order_by(TChatSession.updated_at.asc())
                        .limit(limit)
                    )
                )
                .all()
            )
        processed = 0
        for session, _max_seq in candidates:
            try:
                async with pg_manager.get_async_session_context() as db:
                    done = await MemoryExtractionService.extract_session(
                        db, session_id=session.id, user_id=str(session.user_id)
                    )
                processed += int(done)
            except Exception:
                logger.warning(
                    "memory extraction failed session_id={}（水位未推进，下次 sweep 重试）",
                    session.id,
                )
        return processed

    @staticmethod
    async def extract_session(
        db: AsyncSession, *, session_id: str, user_id: str
    ) -> bool:
        """抽取单个会话水位之后的新消息段；同一用户串行。

        返回是否完成（含禁用/无价值段零写入但推进水位）。
        """
        lock = _lock_for(user_id)
        async with lock:
            session = await db.get(TChatSession, session_id)
            if session is None:
                return False
            watermark = session.memory_extracted_seq
            messages, new_max = await MemoryExtractionService._load_segment(
                db, session_id, watermark
            )
            if not MemoryUserSettings.is_enabled(user_id):
                # 关闭期间不回溯（spec：关闭后新终态不再抽取），水位照推
                await MemoryExtractionService._mark_extracted(db, session_id, new_max)
                return True
            if not messages:
                # 新段无合格文本（纯系统消息等）：推进水位，零写入
                await MemoryExtractionService._mark_extracted(db, session_id, new_max)
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
            await MemoryExtractionService._mark_extracted(db, session_id, new_max)
            return True

    # ----- 输入装载 -----

    @staticmethod
    async def _load_segment(
        db: AsyncSession, session_id: str, watermark: int | None
    ) -> tuple[str, int | None]:
        """装载水位之后的新消息段 + 水位前 2 条衔接背景。

        返回 (输入文本, 本段最大合格序号)。文本超预算时保头（20%，目标
        陈述）保尾（60%，结论），中间标注省略。背景消息仅用于指代消解
        （「之前那个方案」类表述），不参与抽取判定。
        """
        rows = (
            (
                await db.execute(
                    select(
                        TChatMessage.message_sequence,
                        TChatMessage.role,
                        TChatMessage.content,
                    )
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
        if not rows:
            return "", None
        new_max = rows[-1].message_sequence
        rendered = [
            (seq, role, MemoryExtractionService._message_text(content))
            for seq, role, content in rows
        ]
        before = [
            (role, text) for seq, role, text in rendered
            if watermark is not None and seq <= watermark and text
        ]
        after = [
            (role, text) for seq, role, text in rendered
            if (watermark is None or seq > watermark) and text
        ]
        bridge = [f"[背景] [{role}] {text}" for role, text in before[-2:]]
        segment = [f"[{role}] {text}" for role, text in after]

        budget = MemoryConfig.max_message_chars
        if sum(len(line) for line in segment) > budget:
            head_budget, tail_budget = int(budget * 0.2), int(budget * 0.6)
            head: list[str] = []
            head_chars = 0
            tail: list[str] = []
            tail_chars = 0
            for line in segment:
                if head_chars + len(line) <= head_budget:
                    head.append(line)
                    head_chars += len(line)
                else:
                    break
            for line in reversed(segment):
                if tail_chars + len(line) <= tail_budget:
                    tail.insert(0, line)
                    tail_chars += len(line)
                else:
                    break
            segment = [*head, "（中间消息因长度上限省略）", *tail]

        parts = []
        if bridge:
            parts.append("\n\n".join(bridge))
        if segment:
            parts.append("\n\n".join(segment))
        return "\n\n---\n\n".join(parts), new_max

    @staticmethod
    def _message_text(content: object) -> str:
        """multipart 消息提取纯文本（reasoning/工具段丢弃）。

        落库形态为 ``{"parts": [{"type": "text", ...}, ...]}``；旧/兼容
        形态有裸字符串与裸 parts 列表，一并容忍。
        """
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            parts = content.get("parts")
            if isinstance(parts, str):
                return parts
            content = parts if isinstance(parts, list) else []
        if not isinstance(content, list):
            return ""
        texts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(str(part.get("content", "")))
            elif isinstance(part, str):
                texts.append(part)
        return "\n".join(t for t in texts if t)

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
        # LLM 失败直接抛出：调用方不标记「已抽取」，下次 sweep 重试
        # （spec：进程崩溃后系统 SHALL 补扫未抽取会话；失败≠无价值）。
        llm = get_llm(model_id=MemoryConfig.extraction_model or None)
        structured = llm.with_structured_output(ExtractionResult)
        result = await structured.ainvoke(prompt)
        if not isinstance(result, ExtractionResult):
            raise RuntimeError("extraction llm returned unexpected payload")
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
        date_str = datetime.now().astimezone().date().isoformat()
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
    async def _mark_extracted(
        db: AsyncSession, session_id: str, seq: int | None
    ) -> None:
        """推进水位；仅在抽取路径成功后调用（失败抛出则水位不动）。

        显式携带 updated_at 自身以抑制列 onupdate——抽取标记不得改变
        会话列表排序（updated_at 语义 = 用户最后活动，非引擎内部状态）。
        """
        values = {
            "memory_extracted_at": int(time.time() * 1000),
            "updated_at": TChatSession.updated_at,
        }
        if seq is not None:
            values["memory_extracted_seq"] = seq
        await db.execute(
            update(TChatSession)
            .where(TChatSession.id == session_id)
            .values(**values)
        )
        await db.commit()


_SWEEP_TASK: asyncio.Task | None = None


async def start_memory_sweeper() -> None:
    """后台 sweep：idle 会话抽取 + 崩溃补跑（同一循环）；启动 60s 后先扫一次。"""
    global _SWEEP_TASK

    async def _loop() -> None:
        from noesis.services.memory.consolidation import maybe_consolidate

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
            # AutoDream 门控整理：挂在 sweep 尾部顺带评估（双条件才真正执行）
            try:
                await maybe_consolidate()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("memory consolidation gate check failed")
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
