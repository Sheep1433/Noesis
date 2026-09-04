"""会话终态自动记忆抽取（agent-memory-cortex）——水位增量。

流程：sweep 发现 idle 且有新消息越过水位的 root 会话 → 读取
水位之后的新消息段（带水位前 2 条衔接背景，有界截断保头保尾）、
本轮召回清单（run.memory_context，经检索工具聚合）、现有条目 →
LLM 五选一判定 → 轻量合并/新建条目 + journal 追加（情景摘要 +
抽取决策块）→ 推进水位。

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
    description: str = Field(
        default="",
        description="两段式描述：一句话结论 + 分号 + 何时调用（如「偏好表格化简体中文输出；涉及文档/报告/说明输出时调用」）",
    )
    body: str = Field(description="结论正文：陈述句事实，一句话到几句")
    why: str = Field(default="", description="为什么（可选）")
    applicability: str = Field(default="", description="适用条件：何时应用（可选）")
    reason: str = Field(
        default="",
        description="抽取决策理由（一句话：为什么值得记 / 为什么并入既有条目）",
    )
    update_existing_label: str = Field(
        default="",
        description="若为更新/修正既有条目，填该条目的 label；新建留空",
    )
    is_correction: bool = Field(
        default=False,
        description="是否为用户对已召回记忆的修正（修正允许更新已召回条目）",
    )


class ExcludedItem(BaseModel):
    """抽取排除项：内容摘要 + 理由（journal 决策块的原料）。"""

    gist: str = Field(description="被排除内容的一句话摘要")
    reason: str = Field(description="排除理由（如「临时任务状态」「与现有条目重复」「文件本身可得」）")


class ExtractionResult(BaseModel):
    entries: list[ExtractedEntry] = Field(default_factory=list)
    excluded: list[ExcludedItem] = Field(
        default_factory=list,
        description="判定为「不该存」的内容及理由（落入 journal 决策块，不建条目）",
    )
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

## 路由防呆（硬规则）
- 带时效性或阶段性的内容（「正在」「本周」「接下来三个月」「Q3 要完成」）只能进 goal，
  不得写入 preference/decision/experience/gotcha——错误条目越稳定越难被整理淘汰
- 灰色地带对照：
  - 「定了包管理用 pnpm」→ decision；「就是喜欢 pnpm 的简洁」→ preference
  - 「这个做法绕过了缓存失效的坑」→ gotcha；「先复现再定位的三步法很有效」→ experience
  - 「Q3 要做完迁移」→ goal（时效性）；「迁移已完成，最终选了双写方案」→ decision

## 不该存（出现即记入 excluded，含理由）
- grep/读文件/代码本身能得出的信息（文件路径、代码结构、git 历史）
- USER.md/AGENTS.md 已写过的内容（见「现有记忆」）
- 临时任务状态、当前对话上下文、寒暄
- 与「本轮已召回条目」相同的内容（除非 is_correction=true，即用户明确修正了它）

## 规则
- 相对日期改写为绝对日期（「下周」→ 具体日期；今天按 {today} 算）
- label 一律中文短语（2-6 字），英文只出现在 slug_hint
- description 两段式：一句话结论 + 分号 + 何时调用（「是什么；何时调用」），
  不写成正文复读
- 与「现有记忆」语义重复 → 不新建，设 update_existing_label 为该条目 label
- 明显过时的既有条目被会话推翻 → update_existing_label + 新正文
- 每条 entry 附 reason（为什么值得记 / 为什么并入）
- 至多 {max_entries} 条新条目；无长期价值 → entries 留空、journal_summary 也留空
- 敏感内容（密钥、凭据、身份证件等）一律不存（也不进 excluded）
- journal_summary 无论如何概述本会话（除非整场无价值）

## 现有记忆
{existing}

## 本轮已召回条目（复述不算新记忆；用户修正 → is_correction）
{recalled}

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
            recalled = await MemoryExtractionService._load_recalled(db, session_id)
            MemoryStore.ensure_layout(user_id)
            existing = MemoryStore.read_index(user_id)

            result = await MemoryExtractionService._run_llm(
                user_id=user_id,
                messages=messages,
                recalled=recalled,
                existing=existing,
            )
            await MemoryExtractionService._apply(
                user_id=user_id,
                session_id=session_id,
                result=result,
                recalled=recalled,
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
    async def _load_recalled(db: AsyncSession, session_id: str) -> list[str]:
        """聚合本会话各 run 召回清单（run.memory_context['entries']，
        由 search_memory 工具命中后合并写入）。"""
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
        *, user_id: str, messages: str, recalled: list[str], existing
    ) -> ExtractionResult:
        from noesis.llm.factory import get_llm

        existing_lines = [
            f"- [{e.label}] {e.description}（{e.memory_type}/{e.slug}.md）"
            for e in existing.entries
        ] or ["（空）"]
        recalled_lines = [f"- {path}" for path in recalled] or ["（无）"]
        prompt = _EXTRACTION_PROMPT.format(
            today=datetime.now(timezone.utc).date().isoformat(),
            max_entries=MemoryConfig.max_entries_per_extraction,
            existing="\n".join(existing_lines),
            recalled="\n".join(recalled_lines),
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
        recalled: list[str],
    ) -> None:
        date_str = datetime.now().astimezone().date().isoformat()
        source = f"会话 {session_id[:8]} · {date_str}"
        recalled_set = set(recalled)
        new_count = 0
        decisions: list[str] = []
        for candidate in result.entries:
            reason = candidate.reason.strip() or "会话中确认的长期事实"
            if candidate.memory_type not in MEMORY_TYPES:
                decisions.append(
                    f"- 排除：{candidate.label or candidate.body[:24]}"
                    f"（类型 {candidate.memory_type} 不在冻结五类，不入语义层）"
                )
                continue  # 类型不匹配不入语义层
            target = MemoryStore.slug_of(
                user_id,
                candidate.memory_type,
                candidate.update_existing_label or candidate.label,
            )
            if (
                target
                and f"{candidate.memory_type}/{target}.md" in recalled_set
                and not candidate.is_correction
            ):
                decisions.append(
                    f"- 排除：{candidate.label}（复述本轮召回条目，防自强化）"
                )
                continue  # 防自强化：复述召回条目不记录（用户修正除外）
            if target is None:
                if new_count >= MemoryConfig.max_entries_per_extraction:
                    decisions.append(
                        f"- 排除：{candidate.label}（超出单次上限，素材由 journal 覆盖）"
                    )
                    continue
                new_count += 1
            entry = MemoryStore.upsert_entry(
                user_id,
                memory_type=candidate.memory_type,
                label=candidate.label,
                body=candidate.body,
                why=candidate.why,
                applicability=candidate.applicability,
                description=candidate.description,
                sources=[source],
                slug=target or (candidate.slug_hint or None),
                max_entry_chars=MemoryConfig.max_entry_chars,
            )
            op = "更新" if target is not None else "新建"
            decisions.append(f"- {op}：{entry.rel_path}（{reason}）")
        decisions.extend(
            f"- 排除：{item.gist}（{item.reason}）" for item in result.excluded
        )
        if result.journal_summary.strip():
            MemoryStore.append_journal(
                user_id, session_id=session_id, text=result.journal_summary
            )
        # 抽取决策块：写入与排除决策对账（无决策的零价值会话不落块）
        if decisions:
            MemoryStore.append_journal(
                user_id,
                session_id=session_id,
                text="\n".join(decisions),
                label="抽取决策",
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
