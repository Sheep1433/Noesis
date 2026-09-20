"""会话历史检索服务：pg_trgm 确定性全文匹配（session-history-search）。

数据源是权威存储 ``t_chat_message``（压缩只改上下文、不动库）；压缩归档
文件是快照副本，本模块不消费。检索不掺 LLM，结果为库内原文片段。

GIN 索引写入侧调参（idx_message_content_trgm，迁移 202609070001）：
- ``fastupdate = off``：消息表写入为会话节奏（每轮 2 行 + assistant parts
  checkpoint 更新），低写入速率下关闭 pending list——避免首次检索触发
  pending 清理的延迟毛刺，也避免 pending list 膨胀占用内存。
- 迁移收尾 ANALYZE；后续若观察到计划退化（检索走 Seq Scan），先跑
  ``ANALYZE t_chat_message`` 再检查索引膨胀：
  ``SELECT avg_dead_tuple_fraction FROM pgstattuple('t_chat_message')``
  （需 pgstattuple 扩展）；膨胀明显时 REINDEX CONCURRENTLY。
- 短关键词（<3 字符）trigram 索引无法加速，退化为顺序扫描——工具描述
  引导用具体词（路径、错误码、函数名），这是已知取舍。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import Text, cast, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.chat.runs.models import ACTIVE_RUN_STATUSES
from noesis.storage.postgres.models.chat import (
    TAgentRun,
    TChatMessage,
    TChatSession,
)

# 检索输出预算（产品逻辑常量，不随部署变化）：
# 两个检索工具 limit 参数的服务端钳制 / 单条命中截断（超长 assistant 消息
# 含嵌在 parts 里的工具轨迹）/ 单次返回总字符上限（检索不得成为读回全量
# 历史的通道）/ 滚动深读 window 的服务端上限
MAX_HITS = 10
MAX_EXCERPT_CHARS = 2000
MAX_TOTAL_CHARS = 12000
MAX_WINDOW = 10

# 候选放大倍数：SQL ILIKE 命中含 JSON 结构键噪声，Python 侧按渲染文本
# 精确过滤后会淘汰一部分，放大候选保证 top-k 仍有真命中
_CANDIDATE_FACTOR = 3
_MAX_CANDIDATES = 60


class SessionAccessDenied(Exception):
    """会话不存在或不归属当前用户（调用方不得区分两者，防存在性探测）。"""


@dataclass(frozen=True)
class HistoryHit:
    """单条命中：定位信息 + 渲染后的纯文本片段。"""

    sequence: int
    role: str
    created_at: int
    text: str
    truncated: bool


@dataclass(frozen=True)
class SessionSearchResult:
    hits: list[HistoryHit]
    mode: str  # search | scroll
    boundary_unknown: bool
    cutoff_seq: Optional[int]


@dataclass(frozen=True)
class SessionGroup:
    """跨会话发现的单会话分组：血缘信息 + 最强匹配片段。"""

    session_id: str
    title: str
    kind: str
    parent_id: Optional[str]
    created_at: int
    updated_at: int
    matched_sequence: int
    matched_role: str
    fragment: str
    truncated: bool


def render_content_text(content: Any) -> str:
    """JSON multipart content → 纯文本（text/tool parts），不渲染原始 JSON。

    历史数据形状兜底：非 {"parts": [...]} 的 dict / str 原样字符串化。
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        return str(content)
    rendered: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text":
            rendered.append(str(part.get("content") or ""))
        elif ptype == "tool":
            name = str(part.get("name") or "tool")
            arguments = part.get("input")
            output = part.get("output")
            args_text = str(arguments) if arguments is not None else "{}"
            output_text = str(output) if output is not None else ""
            rendered.append(f"[tool:{name}] input: {args_text} output: {output_text}")
    return "\n".join(block for block in rendered if block)


def excerpt_around(text: str, keyword: str, max_chars: int) -> tuple[str, bool]:
    """截断为关键词附近的片段；返回 (片段, 是否截断)。"""
    if len(text) <= max_chars:
        return text, False
    pos = text.lower().find(keyword.lower()) if keyword else -1
    if pos < 0:
        return text[:max_chars], True
    head = max_chars // 4
    start = max(0, pos - head)
    end = min(len(text), start + max_chars)
    start = max(0, end - max_chars)
    return text[start:end], True


def _escape_like(keyword: str) -> str:
    return (
        keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )


def _text_expr():
    return cast(TChatMessage.content, Text)


def _apply_total_budget(
    hits: list[HistoryHit], total_chars: int
) -> list[HistoryHit]:
    """总量上限：超出即截断结果列表（单条已各自截断）。"""
    used = 0
    kept: list[HistoryHit] = []
    for hit in hits:
        if used + len(hit.text) > total_chars and kept:
            break
        used += len(hit.text)
        kept.append(hit)
    return kept


async def _require_owned_session(
    db: AsyncSession, *, session_id: str, user_id: str
) -> TChatSession:
    row = await db.execute(
        select(TChatSession).where(
            TChatSession.id == session_id,
            TChatSession.user_id == user_id,
            TChatSession.deleted_at.is_(None),
        )
    )
    session = row.scalar_one_or_none()
    if session is None:
        raise SessionAccessDenied(session_id)
    return session


async def search_session_history(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: str,
    query: str = "",
    before_compaction: bool = False,
    limit: int = 5,
    around_sequence: Optional[int] = None,
    window: int = 5,
) -> SessionSearchResult:
    """单会话检索：检索形态（query 命中 top-k）或滚动形态（序号 ± window 原文）。

    - 检索形态：ILIKE 经 GIN trigram 索引预筛 + word_similarity 排序取候选，
      Python 侧按渲染文本精确过滤（淘汰 JSON 结构键噪声）后取 top-k，
      返回按序号升序。
    - 滚动形态：around_sequence 给定时忽略 query，返回目标序号前后各至多
      window 条（服务端钳制）原文；before_compaction 同样生效（窗口上界
      钳到边界序号）。
    - before_compaction：只检索 message_sequence <= compaction_cutoff_seq；
      边界缺失（NULL）时降级为全历史并标注 boundary_unknown。
    """
    limit = max(1, min(int(limit), MAX_HITS))
    window = max(1, min(int(window), MAX_WINDOW))
    session = await _require_owned_session(
        db, session_id=session_id, user_id=user_id
    )
    cutoff = session.compaction_cutoff_seq
    boundary_unknown = before_compaction and cutoff is None

    if around_sequence is not None:
        lo = int(around_sequence) - window
        hi = int(around_sequence) + window
        if before_compaction and cutoff is not None:
            hi = min(hi, cutoff)
        rows = (
            await db.execute(
                select(
                    TChatMessage.message_sequence,
                    TChatMessage.role,
                    TChatMessage.created_at,
                    TChatMessage.content,
                )
                .where(
                    TChatMessage.session_id == session_id,
                    TChatMessage.deleted_at.is_(None),
                    TChatMessage.message_sequence >= lo,
                    TChatMessage.message_sequence <= hi,
                )
                .order_by(TChatMessage.message_sequence.asc())
            )
        ).all()
        hits: list[HistoryHit] = []
        for row in rows:
            text = render_content_text(row.content)
            excerpt, truncated = excerpt_around(
                text, "", MAX_EXCERPT_CHARS
            )
            hits.append(
                HistoryHit(
                    sequence=int(row.message_sequence),
                    role=str(row.role),
                    created_at=int(row.created_at),
                    text=excerpt,
                    truncated=truncated,
                )
            )
        return SessionSearchResult(
            hits=_apply_total_budget(hits, MAX_TOTAL_CHARS),
            mode="scroll",
            boundary_unknown=boundary_unknown,
            cutoff_seq=cutoff,
        )

    keyword = (query or "").strip()
    if not keyword:
        raise ValueError("检索形态需要非空 query（或改用 around_sequence 滚动形态）")

    conditions = [
        TChatMessage.session_id == session_id,
        TChatMessage.deleted_at.is_(None),
        _text_expr().ilike(f"%{_escape_like(keyword)}%", escape="\\"),
    ]
    if before_compaction and cutoff is not None:
        conditions.append(TChatMessage.message_sequence <= cutoff)

    candidates = min(limit * _CANDIDATE_FACTOR, _MAX_CANDIDATES)
    rows = (
        await db.execute(
            select(
                TChatMessage.message_sequence,
                TChatMessage.role,
                TChatMessage.created_at,
                TChatMessage.content,
            )
            .where(*conditions)
            .order_by(func.word_similarity(keyword, _text_expr()).desc())
            .limit(candidates)
        )
    ).all()

    hits: list[HistoryHit] = []
    for row in rows:
        text = render_content_text(row.content)
        if keyword.lower() not in text.lower():
            continue
        excerpt, truncated = excerpt_around(
            text, keyword, MAX_EXCERPT_CHARS
        )
        hits.append(
            HistoryHit(
                sequence=int(row.message_sequence),
                role=str(row.role),
                created_at=int(row.created_at),
                text=excerpt,
                truncated=truncated,
            )
        )
        if len(hits) >= limit:
            break
    hits.sort(key=lambda hit: hit.sequence)
    return SessionSearchResult(
        hits=_apply_total_budget(hits, MAX_TOTAL_CHARS),
        mode="search",
        boundary_unknown=boundary_unknown,
        cutoff_seq=cutoff,
    )


async def search_user_sessions(
    db: AsyncSession,
    *,
    user_id: str,
    exclude_session_id: str,
    query: str,
    limit: int = 5,
) -> list[SessionGroup]:
    """跨会话发现：当前用户的历史会话按关键词匹配，按会话分组返回最强片段。

    强制 user_id 归属过滤；排除当前会话（当前会话由 search_history 默认
    行为覆盖）。分组按最强命中排序（word_similarity 候选序）。
    """
    limit = max(1, min(int(limit), MAX_HITS))
    keyword = (query or "").strip()
    if not keyword:
        raise ValueError("search_sessions 需要非空 query")

    rows = (
        await db.execute(
            select(
                TChatMessage.session_id,
                TChatMessage.message_sequence,
                TChatMessage.role,
                TChatMessage.content,
                TChatSession.title,
                TChatSession.kind,
                TChatSession.parent_id,
                TChatSession.created_at,
                TChatSession.updated_at,
            )
            .join(TChatSession, TChatSession.id == TChatMessage.session_id)
            .where(
                TChatSession.user_id == user_id,
                TChatSession.deleted_at.is_(None),
                TChatMessage.deleted_at.is_(None),
                TChatMessage.session_id != exclude_session_id,
                _text_expr().ilike(f"%{_escape_like(keyword)}%", escape="\\"),
            )
            .order_by(func.word_similarity(keyword, _text_expr()).desc())
            .limit(min(limit * _CANDIDATE_FACTOR * 2, _MAX_CANDIDATES * 2))
        )
    ).all()

    groups: dict[str, SessionGroup] = {}
    used_chars = 0
    for row in rows:
        if row.session_id in groups:
            continue
        text = render_content_text(row.content)
        if keyword.lower() not in text.lower():
            continue
        fragment, truncated = excerpt_around(
            text, keyword, MAX_EXCERPT_CHARS
        )
        # 总量上限与单会话检索同口径：片段总和超预算即截断结果
        if used_chars + len(fragment) > MAX_TOTAL_CHARS and groups:
            break
        used_chars += len(fragment)
        groups[row.session_id] = SessionGroup(
            session_id=str(row.session_id),
            title=str(row.title),
            kind=str(row.kind),
            parent_id=row.parent_id,
            created_at=int(row.created_at),
            updated_at=int(row.updated_at),
            matched_sequence=int(row.message_sequence),
            matched_role=str(row.role),
            fragment=fragment,
            truncated=truncated,
        )
        if len(groups) >= limit:
            break
    return list(groups.values())


async def record_compaction_boundary(
    db: AsyncSession, *, session_id: str
) -> Optional[int]:
    """压缩完成时写遮蔽边界：排除活跃 run 的 streaming 骨架行后取最大序号。

    口径：被压缩遮蔽的消息必然 <= 该值（压缩只遮蔽已终态的库内消息）；
    反向不保证精确——边界可能略含仍在保留尾的近期消息，宁多标不漏标
    （before_compaction 语义是"只搜旧历史"，多含一条近期消息无害）。
    graph 消息 id 与 DB 行 id 无关联、ToolMessage 无独立行，逐条精确
    映射不可行，故取保守上界。写失败由调用方记日志降级（边界缺失 =
    检索时"边界未知"全历史），不阻断压缩主流程。
    """
    active_skeletons = select(TAgentRun.assistant_message_id).where(
        TAgentRun.session_id == session_id,
        TAgentRun.status.in_(ACTIVE_RUN_STATUSES),
    )
    row = await db.execute(
        select(func.max(TChatMessage.message_sequence)).where(
            TChatMessage.session_id == session_id,
            TChatMessage.deleted_at.is_(None),
            TChatMessage.id.not_in(active_skeletons),
        )
    )
    max_seq = row.scalar_one_or_none()
    if max_seq is None:
        return None
    await db.execute(
        update(TChatSession)
        .where(
            TChatSession.id == session_id,
            (TChatSession.compaction_cutoff_seq.is_(None))
            | (TChatSession.compaction_cutoff_seq < max_seq),
        )
        .values(compaction_cutoff_seq=max_seq)
    )
    await db.commit()
    return int(max_seq)


__all__ = [
    "HistoryHit",
    "SessionAccessDenied",
    "SessionGroup",
    "SessionSearchResult",
    "record_compaction_boundary",
    "render_content_text",
    "search_session_history",
    "search_user_sessions",
]
