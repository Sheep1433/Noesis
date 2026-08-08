"""将跨会话聊天整理为按日、可追溯的 L2 记忆。"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import date as Date, datetime, time, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.config.user_data_paths import ensure_user_memory_dir, get_user_daily_memory_path
from noesis.storage.postgres.models.chat import TChatMessage, TChatSession

_META_PREFIX = "<!-- noesis-memory:"
_MAX_TEXT = 1200
_MAX_SUMMARY = 360
_WORD_RE = re.compile(r"[\w\u4e00-\u9fff]{2,}")


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for part in content:
        if not isinstance(part, dict) or part.get("type") not in {"text", "text-delta"}:
            continue
        value = part.get("text") or part.get("content") or ""
        if isinstance(value, str) and value.strip():
            chunks.append(value.strip())
    return "\n".join(chunks).strip()


def _compact(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    return text if len(text) <= limit else f"{text[:limit - 1]}…"


def _category(text: str) -> str:
    lowered = text.casefold()
    if any(term in lowered for term in ("决定", "采用", "选择", "方案")):
        return "decision"
    if any(term in lowered for term in ("偏好", "喜欢", "不要", "习惯")):
        return "preference"
    if any(term in lowered for term in ("待办", "下一步", "需要", "计划")):
        return "todo"
    if any(term in lowered for term in ("报错", "失败", "修复", "bug", "error")):
        return "problem"
    return "fact"


def _keywords(text: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for word in _WORD_RE.findall(text.casefold()):
        if word.isdigit() or word in seen:
            continue
        seen.add(word)
        result.append(word)
        if len(result) == 8:
            break
    return result


@dataclass(frozen=True)
class DreamMessage:
    message_id: str
    session_id: str
    role: str
    text: str
    created_at: int


def build_entries(user_id: str | int, messages: list[DreamMessage]) -> list[dict[str, Any]]:
    """将消息按 user→assistant 组合成稳定条目。"""
    entries: list[dict[str, Any]] = []
    pending: DreamMessage | None = None
    for message in messages:
        if message.role == "user":
            pending = message
            continue
        if message.role != "assistant" or pending is None or message.session_id != pending.session_id:
            continue
        combined = f"用户：{_compact(pending.text, _MAX_TEXT)}\n回复：{_compact(message.text, _MAX_TEXT)}"
        digest = hashlib.sha256(
            f"{user_id}:{pending.message_id}:{message.message_id}:{combined}".encode()
        ).hexdigest()[:16]
        entries.append({
            "id": digest,
            "category": _category(combined),
            "summary": _compact(combined, _MAX_SUMMARY),
            "keywords": _keywords(combined),
            "sources": [
                {"session_id": pending.session_id, "message_id": pending.message_id},
                {"session_id": message.session_id, "message_id": message.message_id},
            ],
            "created_at": pending.created_at,
        })
        pending = None
    unique = {entry["id"]: entry for entry in entries}
    return sorted(unique.values(), key=lambda item: (item["created_at"], item["id"]))


def render_daily_memory(target_date: str, timezone_name: str, entries: list[dict[str, Any]]) -> str:
    lines = [
        f"# {target_date} 记忆",
        "",
        f"<!-- noesis-dream:date={target_date};timezone={timezone_name};status=complete -->",
        "",
    ]
    if not entries:
        lines.extend(["当天没有可整理的记忆。", ""])
    for entry in entries:
        meta = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        lines.extend([
            f"## {entry['id']}",
            f"{_META_PREFIX}{meta} -->",
            f"- 分类：{entry['category']}",
            f"- 关键词：{', '.join(entry['keywords']) or '无'}",
            f"- 摘要：{entry['summary']}",
            "",
        ])
    return "\n".join(lines)


def parse_daily_entries(content: str, target_date: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in content.splitlines():
        if not line.startswith(_META_PREFIX) or not line.endswith(" -->"):
            continue
        try:
            item = json.loads(line[len(_META_PREFIX):-4])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(item, dict) and item.get("id") and item.get("summary"):
            item["date"] = target_date
            entries.append(item)
    return entries


class MemoryDreamService:
    @staticmethod
    def _day_range(target_date: str, timezone_name: str) -> tuple[int, int]:
        try:
            day = Date.fromisoformat(target_date)
            tz = ZoneInfo(timezone_name)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("日期或时区无效") from exc
        start = datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc)
        end = (datetime.combine(day, time.min, tzinfo=tz) + timedelta(days=1)).astimezone(timezone.utc)
        return int(start.timestamp() * 1000), int(end.timestamp() * 1000)

    @classmethod
    async def run(cls, db: AsyncSession, *, user_id: str | int, target_date: str, timezone_name: str = "Asia/Shanghai") -> dict[str, Any]:
        start_ms, end_ms = cls._day_range(target_date, timezone_name)
        result = await db.execute(
            select(TChatMessage)
            .join(TChatSession, TChatSession.id == TChatMessage.session_id)
            .where(and_(
                TChatMessage.user_id == user_id,
                TChatSession.user_id == user_id,
                TChatMessage.deleted_at.is_(None),
                TChatSession.deleted_at.is_(None),
                TChatMessage.status == "completed",
                TChatMessage.role.in_(("user", "assistant")),
                TChatMessage.created_at >= start_ms,
                TChatMessage.created_at < end_ms,
            ))
            .order_by(TChatMessage.session_id, TChatMessage.created_at, TChatMessage.id)
        )
        messages = [
            DreamMessage(str(row.id), str(row.session_id), row.role, _text_content(row.content), row.created_at)
            for row in result.scalars().all()
            if _text_content(row.content)
        ]
        entries = build_entries(user_id, messages)
        root = ensure_user_memory_dir(user_id)
        target = get_user_daily_memory_path(user_id, target_date)
        body = render_daily_memory(target_date, timezone_name, entries)
        with NamedTemporaryFile("w", encoding="utf-8", dir=root, prefix=f".{target.name}.", delete=False) as handle:
            handle.write(body)
            temp_path = Path(handle.name)
        os.replace(temp_path, target)
        return {"date": target_date, "timezone": timezone_name, "entries": len(entries), "status": "complete"}

    @staticmethod
    async def get_source(db: AsyncSession, *, user_id: str | int, session_id: str, message_id: str, context_messages: int = 1) -> dict[str, Any]:
        context_messages = max(0, min(context_messages, 3))
        ownership = await db.execute(select(TChatSession).where(and_(
            TChatSession.id == session_id,
            TChatSession.user_id == user_id,
            TChatSession.deleted_at.is_(None),
        )))
        if ownership.scalar_one_or_none() is None:
            raise LookupError("记忆来源不存在")
        rows = (await db.execute(
            select(TChatMessage).where(and_(
                TChatMessage.session_id == session_id,
                TChatMessage.user_id == user_id,
                TChatMessage.deleted_at.is_(None),
                TChatMessage.status == "completed",
                TChatMessage.role.in_(("user", "assistant")),
            )).order_by(TChatMessage.created_at, TChatMessage.id)
        )).scalars().all()
        index = next((i for i, row in enumerate(rows) if str(row.id) == message_id), None)
        if index is None:
            raise LookupError("记忆来源不存在")
        selected = rows[max(0, index - context_messages):index + context_messages + 1]
        return {"session_id": session_id, "message_id": message_id, "messages": [
            {"message_id": str(row.id), "role": row.role, "text": _compact(_text_content(row.content), _MAX_TEXT), "created_at": row.created_at}
            for row in selected if _text_content(row.content)
        ]}
