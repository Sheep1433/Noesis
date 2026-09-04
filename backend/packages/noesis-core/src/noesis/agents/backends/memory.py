"""`/memory/` 白名单 backend：显式文件 + 记忆条目（HITL 写入）。"""

from __future__ import annotations

import re
from pathlib import Path

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.utils import file_data_to_string
from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from noesis.agents.backends.paths import posix_clean
from noesis.services.memory.store import IndexEntry, MemoryStore
from noesis.services.memory.types import MEMORY_TYPES

_ROOT_FILES = frozenset({"AGENTS.md", "USER.md"})
_TYPE_DIRS = frozenset(MEMORY_TYPES)
_ENTRY_RE = re.compile(r"^/(%s)/([A-Za-z0-9_-]+)\.md$" % "|".join(_TYPE_DIRS))
_JOURNAL_RE = re.compile(r"^/journal/\d{4}-\d{2}-\d{2}\.md$")
_PERMISSION_DENIED = "permission_denied"


def _memory_key(file_path: str) -> str:
    """Composite 已剥 ``/memory/``；只做轻量规范化，勿套 /workspace。"""
    text = (file_path or "").strip().replace("\\", "/")
    if not text.startswith("/"):
        text = f"/{text}"
    return posix_clean(text)


def _host_key(key: str) -> str:
    """backend key → 实际文件系统 key（backend root = 用户数据根）。

    根文件（AGENTS.md/USER.md）直接落用户根；其余（MEMORY.md、
    条目、journal）落在 memory/ 子树。
    """
    name = key.lstrip("/")
    if name in _ROOT_FILES:
        return key
    return f"/memory/{name}"


def _is_entry(key: str) -> bool:
    return bool(_ENTRY_RE.match(key))


def _readable(key: str) -> bool:
    name = key.lstrip("/")
    return (
        name in _ROOT_FILES
        or name == "MEMORY.md"
        or _is_entry(key)
        or bool(_JOURNAL_RE.match(key))
    )


def _writable(key: str) -> bool:
    name = key.lstrip("/")
    return name in _ROOT_FILES or _is_entry(key)


def is_memory_writable_path(path: str) -> bool:
    """agent 可见路径是否在记忆写入白名单内（根文件 + 五类条目目录）。

    HITL 的 memory_write_when 据此判定：白名单外的 /memory 写入不触发
    审批——guard 必然拒绝，让模型直接收到拒绝反馈，而不是让用户批准
    一笔注定失败的写入。入参是 agent 可见的完整路径（含 /memory 前缀），
    这里先归一化并剥掉路由前缀，与 CompositeBackend 派发到 memory
    backend 时的路径形态一致。
    """
    from noesis.agents.backends.paths import AGENT_MEMORY_ROUTE, canonicalize_agent_path

    raw = (path or "").strip()
    if not raw:
        return False
    try:
        normalized = canonicalize_agent_path(raw)
    except ValueError:
        return False
    root = AGENT_MEMORY_ROUTE.rstrip("/")
    prefix = root + "/"
    if normalized == root:
        key = "/"
    elif normalized.startswith(prefix):
        key = "/" + normalized[len(prefix):]
    else:
        return False
    return _writable(_memory_key(key))


class GuardedFilesystemBackend(BackendProtocol):
    """限制可见/可写路径：根文件 + 记忆条目可写，索引与 journal 只读。"""

    def __init__(
        self,
        inner: FilesystemBackend,
        *,
        user_id: str,
        allowed: frozenset[str],
    ) -> None:
        self._inner = inner
        self._user_id = user_id
        self._allowed = allowed  # 根文件白名单（保留旧契约参数形态）

    def _resolve(self, file_path: str) -> str | None:
        key = _memory_key(file_path)
        return key if _readable(key) else None

    def ls(self, path: str) -> LsResult:
        key = _memory_key(path)
        if key == "/":
            result = self._inner.ls("/")
            if result.error:
                return result
            entries = [
                e for e in (result.entries or []) if e.get("path", "").lstrip("/") in _ROOT_FILES
            ]
            entries.append({"path": "/MEMORY.md", "type": "file"})
            for type_dir in _TYPE_DIRS:
                entries.append({"path": f"/{type_dir}", "type": "directory"})
            entries.append({"path": "/journal", "type": "directory"})
            return LsResult(entries=entries)
        if key.lstrip("/") in _TYPE_DIRS or key.lstrip("/") == "journal":
            result = self._inner.ls(_host_key(key))
            if result.error:
                return result
            return LsResult(
                entries=[
                    {**e, "path": key.rstrip("/") + "/" + str(e.get("path", "")).rstrip("/").split("/")[-1]}
                    for e in (result.entries or [])
                    if str(e.get("path", "")).endswith(".md")
                ]
            )
        return LsResult(entries=None, error="path_not_found")

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        key = self._resolve(file_path)
        if not key:
            return ReadResult(error="file_not_found")
        return self._inner.read(_host_key(key), offset=offset, limit=limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        """记忆写入为显式 upsert；条目写入后自动同步索引行。

        根文件沿用「先读后写整文件替换」契约（见
        tests/test_user_memory_backend.py）；条目文件为新增/覆盖，
        均经 interrupt_on write_file 审批。
        """
        key = _memory_key(file_path)
        if not _writable(key):
            return WriteResult(
                error="file_not_found" if not _readable(key) else _PERMISSION_DENIED
            )
        host = _host_key(key)
        if key.lstrip("/") in _ROOT_FILES:
            existing = self._inner.read(host, offset=0, limit=100_000)
            if existing.error is None:
                edited = self._inner.edit(
                    host, file_data_to_string(existing.file_data), content
                )
                if edited.error:
                    return WriteResult(error=edited.error)
                return WriteResult(path=edited.path or file_path)
            return self._inner.write(host, content)

        result = self._inner.write(host, content)
        if result.error is None and _is_entry(key):
            self._sync_index_line(key)
        return result

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        key = _memory_key(file_path)
        if not _writable(key):
            return EditResult(
                error="file_not_found" if not _readable(key) else _PERMISSION_DENIED
            )
        result = self._inner.edit(
            _host_key(key), old_string, new_string, replace_all=replace_all
        )
        if result.error is None and _is_entry(key):
            self._sync_index_line(key)
        return result

    def _sync_index_line(self, key: str) -> None:
        """条目写入后维护索引一致性（引擎职责，不让模型手编索引）。"""
        try:
            match = _ENTRY_RE.match(key)
            assert match
            front = MemoryStore.read_entry_file(
                self._inner.cwd / "memory" / match[1] / f"{match[2]}.md"
            )
            MemoryStore._sync_index_line(
                self._user_id,
                IndexEntry(
                    memory_type=match[1],
                    slug=match[2],
                    label=str(front.get("label") or match[2]),
                    description=str(front.get("description") or ""),
                ),
            )
        except Exception:
            # 索引同步失败不阻断已确认的写入；整理任务会重建索引兜底
            pass

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        matches: list[dict] = []
        base = _memory_key(path or "/")
        candidates: list[str] = ["MEMORY.md", "AGENTS.md", "USER.md"]
        if base == "/" or base == "/journal":
            candidates += [f"journal/{p.name}" for p in MemoryStore.memory_root(self._user_id).joinpath("journal").glob("*.md")]
        else:
            candidates.append(base.lstrip("/"))
        needle = pattern.casefold()
        for rel in candidates:
            key = f"/{rel}"
            if not _readable(key):
                continue
            read = self._inner.read(_host_key(key), offset=0, limit=100_000)
            if read.error is not None:
                continue
            text = file_data_to_string(read.file_data)
            for line_no, line in enumerate(text.splitlines(), start=1):
                if needle in line.casefold():
                    matches.append({"path": key, "line": line_no, "content": line.strip()})
        return GrepResult(matches=matches[:50])

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        """按 FileInfo 契约返回（deepagents CompositeBackend 对路由命中路径
        做 _remap_file_info_path 字典重映射，裸字符串会 TypeError——
        /memory 上的 glob 曾因此 9ms 即败且归类 unknown）。"""
        base = _memory_key(path)
        wanted = pattern.lstrip("/")
        matches: list[FileInfo] = [{"path": "/MEMORY.md", "type": "file"}]
        for type_dir in _TYPE_DIRS:
            for file_path in MemoryStore.memory_root(self._user_id).joinpath(type_dir).glob(wanted or "*.md"):
                matches.append({"path": f"/{type_dir}/{file_path.name}", "type": "file"})
        return GlobResult(matches=matches[:100])

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [
            FileUploadResponse(
                path=agent_path,
                error=(
                    _PERMISSION_DENIED
                    if _readable(_memory_key(agent_path)) and not _writable(_memory_key(agent_path))
                    else "file_not_found"
                ),
            )
            for agent_path, _ in files
        ]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for agent_path in paths:
            key = _memory_key(agent_path)
            if not _readable(key):
                responses.append(FileDownloadResponse(path=agent_path, content=None, error="file_not_found"))
                continue
            batch = self._inner.download_files([_host_key(key)])
            responses.append(
                batch[0] if batch else FileDownloadResponse(path=agent_path, content=None, error="file_not_found")
            )
        return responses


def UserMemoryBackend(*, agents_path: Path, user_path: Path, user_id: str | None = None) -> GuardedFilesystemBackend:
    """`/memory/`：AGENTS.md 与 USER.md 可写；条目文件可写（自动同步索引）。"""
    memory_root = agents_path.parent
    if user_path.parent != memory_root:
        msg = "AGENTS.md and USER.md must share the same parent directory"
        raise ValueError(msg)
    return GuardedFilesystemBackend(
        FilesystemBackend(root_dir=memory_root, virtual_mode=True),
        user_id=str(user_id) if user_id is not None else memory_root.name,
        allowed=_ROOT_FILES,
    )


__all__ = ["GuardedFilesystemBackend", "UserMemoryBackend", "is_memory_writable_path"]
