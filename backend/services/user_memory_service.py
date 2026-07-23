"""用户级记忆文件 Service（USER.md / AGENTS.md / L2 日记路径）。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Literal

from config.user_data_paths import (
    ensure_user_memory_files,
    get_user_agents_md_path,
    get_user_daily_memory_path,
    get_user_profile_md_path,
    ensure_user_memory_dir,
)

MemoryFileName = Literal["USER.md", "AGENTS.md"]
_ALLOWED_FILES: frozenset[str] = frozenset({"USER.md", "AGENTS.md"})
_MAX_BYTES = 512 * 1024


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
        return {
            "file": file,
            "content": content,
            "updated_at": mtime,
            "size": len(content.encode("utf-8")),
        }

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
    def ensure_daily_dir(user_id: str | int) -> str:
        return str(ensure_user_memory_dir(user_id))

    @staticmethod
    def daily_path(user_id: str | int, date: str) -> str:
        return str(get_user_daily_memory_path(user_id, date))
