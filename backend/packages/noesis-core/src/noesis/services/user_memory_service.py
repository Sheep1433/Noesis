"""用户级记忆文件 Service（USER.md / AGENTS.md / L2 日记路径）。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Dict, Literal

from noesis.config.user_data_paths import (
    ensure_user_memory_files,
    get_user_agents_md_path,
    get_user_daily_memory_path,
    get_user_memory_dir,
    get_user_profile_md_path,
    ensure_user_memory_dir,
)

MemoryFileName = Literal["USER.md", "AGENTS.md"]
_ALLOWED_FILES: frozenset[str] = frozenset({"USER.md", "AGENTS.md"})
_MAX_BYTES = 512 * 1024
_DATE_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")


class UserMemoryService:
    @staticmethod
    def _resolve_path(user_id: str | int, file: str) -> Path:
        if file not in _ALLOWED_FILES:
            raise ValueError(f"非法记忆文件名: {file!r}，仅允许 USER.md / AGENTS.md")
        ensure_user_memory_files(user_id)
        if file == "USER.md":
            return get_user_profile_md_path(user_id)
        return get_user_agents_md_path(user_id)

    @classmethod
    def read_file(cls, user_id: str | int, file: str) -> Dict[str, Any]:
        path = cls._resolve_path(user_id, file)
        content = path.read_text(encoding="utf-8") if path.is_file() else ""
        mtime = None
        if path.is_file():
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        result = {
            "file": file,
            "content": content,
            "updated_at": mtime,
            "size": len(content.encode("utf-8")),
        }
        return result

    @classmethod
    def write_file(cls, user_id: str | int, file: str, content: str) -> Dict[str, Any]:
        if content is None:
            raise ValueError("content 不能为空")
        raw = content if isinstance(content, str) else str(content)
        if len(raw.encode("utf-8")) > _MAX_BYTES:
            raise ValueError(f"文件过大，上限 {_MAX_BYTES} bytes")
        path = cls._resolve_path(user_id, file)
        path.write_text(raw, encoding="utf-8")
        return cls.read_file(user_id, file)

    @staticmethod
    def list_daily(user_id: str | int) -> list[Dict[str, Any]]:
        root = ensure_user_memory_dir(user_id).resolve()
        items: list[Dict[str, Any]] = []
        for path in root.glob("*.md"):
            match = _DATE_FILE_RE.fullmatch(path.name)
            if not match or path.resolve().parent != root:
                continue
            stat = path.stat()
            items.append({"date": match.group(1), "size": stat.st_size, "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()})
        return sorted(items, key=lambda item: item["date"], reverse=True)

    @staticmethod
    def search_daily(user_id: str | int, query: str, limit: int = 20) -> list[Dict[str, Any]]:
        term = str(query or "").strip()
        if not term:
            raise ValueError("请输入搜索关键词")
        if len(term) > 100:
            raise ValueError("搜索关键词过长")
        root = get_user_memory_dir(user_id).resolve()
        if not root.is_dir():
            return []
        matches: list[Dict[str, Any]] = []
        for item in UserMemoryService.list_daily(user_id):
            path = get_user_daily_memory_path(user_id, item["date"]).resolve()
            if path.parent != root:
                raise ValueError("非法日记路径")
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                index = line.casefold().find(term.casefold())
                if index < 0:
                    continue
                start = max(0, index - 60)
                snippet = line[start:index + len(term) + 100].strip()
                matches.append({"date": item["date"], "line": line_no, "snippet": snippet})
                if len(matches) >= max(1, min(limit, 50)):
                    return matches
        return matches

    @staticmethod
    def search_entries(user_id: str | int, query: str, *, date_from: str | None = None, date_to: str | None = None, category: str | None = None, limit: int = 10) -> list[Dict[str, Any]]:
        from noesis.services.memory_dream_service import parse_daily_entries

        term = str(query or "").strip().casefold()
        if not term:
            raise ValueError("请输入搜索关键词")
        if date_from and date_to and date_from > date_to:
            raise ValueError("开始日期不能晚于结束日期")
        scored: list[tuple[int, Dict[str, Any]]] = []
        for daily in UserMemoryService.list_daily(user_id):
            day = daily["date"]
            if (date_from and day < date_from) or (date_to and day > date_to):
                continue
            content = get_user_daily_memory_path(user_id, day).read_text(encoding="utf-8")
            for entry in parse_daily_entries(content, day):
                if category and entry.get("category") != category:
                    continue
                summary = str(entry.get("summary", ""))
                keywords = [str(item) for item in entry.get("keywords", [])]
                score = summary.casefold().count(term) * 3 + sum(term in item.casefold() for item in keywords) * 5
                if score:
                    entry["score"] = score
                    scored.append((score, entry))
        scored.sort(key=lambda pair: (pair[0], pair[1]["date"]), reverse=True)
        return [item for _, item in scored[:max(1, min(limit, 50))]]

    @staticmethod
    def ensure_daily_dir(user_id: str | int) -> str:
        return str(ensure_user_memory_dir(user_id))

    @staticmethod
    def daily_path(user_id: str | int, date: str) -> str:
        return str(get_user_daily_memory_path(user_id, date))
