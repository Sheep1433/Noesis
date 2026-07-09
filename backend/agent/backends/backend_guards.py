"""FilesystemBackend 守卫：路径白名单、静态目录 listing。"""

from __future__ import annotations

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

from agent.backends.prefix_backend import _agent_path

_READ_ONLY_SKILLS_ERROR = "Skills directory is read-only"
_MEMORY_FILES = frozenset({"AGENTS.md", "USER.md"})


class StaticListingBackend(BackendProtocol):
    """只读虚拟目录：根路径固定 ls 条目，其余操作一律拒绝。"""

    def __init__(self, entries: tuple[FileInfo, ...], *, route: str) -> None:
        self._entries = entries
        self._route = route

    def ls(self, path: str) -> LsResult:
        if _agent_path(path) in ("/", ""):
            return LsResult(entries=list(self._entries))
        return LsResult(
            entries=None,
            error=f"Path '{self._route}{path.lstrip('/')}': path_not_found",
        )

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return ReadResult(error=_READ_ONLY_SKILLS_ERROR)

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=_READ_ONLY_SKILLS_ERROR)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return EditResult(error=_READ_ONLY_SKILLS_ERROR)

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        return GrepResult(matches=[])

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        return GlobResult(matches=[])

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [FileDownloadResponse(path=path, content=None, error="file_not_found") for path in paths]


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
        name = _agent_path(file_path).lstrip("/")
        return name if name in self._allowed else None

    def ls(self, path: str) -> LsResult:
        if _agent_path(path) not in ("/", ""):
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
