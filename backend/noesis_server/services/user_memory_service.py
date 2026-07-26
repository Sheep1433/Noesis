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
_PROFILE_START = "<!-- noesis-profile:start -->"
_PROFILE_END = "<!-- noesis-profile:end -->"
_PROFILE_FIELDS = ("称呼", "时区", "语言", "角色")
_DATE_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")


class UserMemoryService:
    @staticmethod
    def _parse_profile(content: str) -> Dict[str, Any]:
        start = content.find(_PROFILE_START)
        end = content.find(_PROFILE_END)
        if start < 0 and "<!-- Noesis 用户画像：可在上下文面板编辑，Agent 只读 -->" in content and "## 基本信息" in content:
            return {"structured_editable": True, "fields": {"称呼": "", "时区": "Asia/Shanghai", "语言": "中文", "角色": ""}, "reason": None}
        if start < 0 or end < start or content.find(_PROFILE_START, start + 1) >= 0:
            return {"structured_editable": False, "fields": {}, "reason": "原文不包含可安全维护的画像区块"}
        block = content[start + len(_PROFILE_START):end]
        fields: Dict[str, str] = {}
        for line in block.splitlines():
            if not line.strip():
                continue
            key, separator, value = line.partition(":")
            if not separator or key.strip() not in _PROFILE_FIELDS or key.strip() in fields:
                return {"structured_editable": False, "fields": {}, "reason": "画像区块格式无法无损解析"}
            fields[key.strip()] = value.strip()
        if set(fields) != set(_PROFILE_FIELDS):
            return {"structured_editable": False, "fields": {}, "reason": "画像区块字段不完整"}
        return {"structured_editable": True, "fields": fields, "reason": None}

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
        if file == "USER.md":
            result["profile"] = cls._parse_profile(content)
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

    @classmethod
    def write_profile_fields(cls, user_id: str | int, fields: Dict[str, Any], expected_updated_at: str | None = None) -> Dict[str, Any]:
        current = cls.read_file(user_id, "USER.md")
        if expected_updated_at and current.get("updated_at") != expected_updated_at:
            raise RuntimeError("画像原文已更新，请重新加载后再保存")
        profile = current["profile"]
        if not profile["structured_editable"]:
            raise RuntimeError(profile["reason"])
        merged = {key: str(fields.get(key, profile["fields"][key]) or "").replace("\n", " ").strip() for key in _PROFILE_FIELDS}
        content = current["content"]
        block = "\n".join([_PROFILE_START, *(f"{key}: {merged[key]}" for key in _PROFILE_FIELDS), _PROFILE_END])
        if _PROFILE_START in content:
            start = content.index(_PROFILE_START)
            end = content.index(_PROFILE_END, start) + len(_PROFILE_END)
            updated = content[:start] + block + content[end:]
        else:
            first_break = content.find("\n")
            updated = content[:first_break + 1] + "\n" + block + content[first_break + 1:]
        return cls.write_file(user_id, "USER.md", updated)

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
    def ensure_daily_dir(user_id: str | int) -> str:
        return str(ensure_user_memory_dir(user_id))

    @staticmethod
    def daily_path(user_id: str | int, date: str) -> str:
        return str(get_user_daily_memory_path(user_id, date))
