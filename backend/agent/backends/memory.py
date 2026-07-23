"""`/memory/` 白名单 backend：仅 AGENTS.md / USER.md。"""

from __future__ import annotations

from pathlib import Path

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.utils import file_data_to_string
from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from agent.backends.paths import posix_clean

_MEMORY_FILES = frozenset({"AGENTS.md", "USER.md"})


def _memory_key(file_path: str) -> str:
    """Composite 已剥 ``/memory/``；只做轻量规范化，勿套 /workspace。"""
    text = (file_path or "").strip().replace("\\", "/")
    if not text.startswith("/"):
        text = f"/{text}"
    return posix_clean(text)


class GuardedFilesystemBackend(BackendProtocol):
    """在 FilesystemBackend 上限制可见/可写路径。"""

    def __init__(
        self,
        inner: FilesystemBackend,
        *,
        allowed: frozenset[str],
        read_only: frozenset[str] = frozenset(),
        read_only_error: str = "permission_denied",
    ) -> None:
        self._inner = inner
        self._allowed = allowed
        self._read_only = read_only
        self._read_only_error = read_only_error

    def _basename(self, file_path: str) -> str | None:
        name = _memory_key(file_path).lstrip("/")
        return name if name in self._allowed else None

    def ls(self, path: str) -> LsResult:
        if _memory_key(path) != "/":
            return LsResult(entries=None, error="path_not_found")
        result = self._inner.ls("/")
        if result.error:
            return result
        allowed_paths = {f"/{name}" for name in self._allowed}
        return LsResult(entries=[e for e in (result.entries or []) if e.get("path") in allowed_paths])

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        if not self._basename(file_path):
            return ReadResult(error="file_not_found")
        return self._inner.read(file_path, offset=offset, limit=limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        name = self._basename(file_path)
        if name in self._read_only:
            return WriteResult(error=self._read_only_error)
        if not name:
            return WriteResult(error="file_not_found")
        created = self._inner.write(file_path, content)
        if created.error is None:
            return created
        if "already exists" not in (created.error or ""):
            return created
        existing = self._inner.read(file_path, offset=0, limit=100_000)
        if existing.error is not None:
            return WriteResult(error=existing.error)
        edited = self._inner.edit(file_path, file_data_to_string(existing.file_data), content)
        if edited.error:
            return WriteResult(error=edited.error)
        return WriteResult(path=edited.path or file_path)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        name = self._basename(file_path)
        if name in self._read_only:
            return EditResult(error=self._read_only_error)
        if not name:
            return EditResult(error="file_not_found")
        return self._inner.edit(file_path, old_string, new_string, replace_all=replace_all)

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        return GrepResult(matches=[])

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        return GlobResult(matches=[])

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [
            FileUploadResponse(
                path=agent_path,
                error="permission_denied" if self._basename(agent_path) in self._read_only else "file_not_found",
            )
            for agent_path, _ in files
        ]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for agent_path in paths:
            if not self._basename(agent_path):
                responses.append(FileDownloadResponse(path=agent_path, content=None, error="file_not_found"))
                continue
            batch = self._inner.download_files([agent_path])
            responses.append(
                batch[0] if batch else FileDownloadResponse(path=agent_path, error="file_not_found")
            )
        return responses


def UserMemoryBackend(*, agents_path: Path, user_path: Path) -> GuardedFilesystemBackend:
    """`/memory/`：AGENTS.md 与 USER.md 均可写。"""
    memory_root = agents_path.parent
    if user_path.parent != memory_root:
        msg = "AGENTS.md and USER.md must share the same parent directory"
        raise ValueError(msg)
    return GuardedFilesystemBackend(
        FilesystemBackend(root_dir=memory_root, virtual_mode=True),
        allowed=_MEMORY_FILES,
    )
