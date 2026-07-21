"""Composite route 适配：route 内相对路径 ↔ inner（local 虚拟路径或容器绝对路径）。"""

from __future__ import annotations

from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)

_READ_ONLY_SKILLS_ERROR = "Skills directory is read-only"


def _agent_path(path: str) -> str:
    return path if path.startswith("/") else f"/{path}"


class PrefixBackend(SandboxBackendProtocol):
    """Composite route 适配：route 内相对路径 ↔ inner（local 虚拟路径或容器绝对路径）。"""

    def __init__(
        self,
        inner: BackendProtocol,
        *,
        container_prefix: str | None = None,
        read_only: bool = False,
    ) -> None:
        self._inner = inner
        self._container_prefix = container_prefix.rstrip("/") if container_prefix else None
        self._read_only = read_only

    def _map_in(self, path: str) -> str:
        if self._container_prefix is None:
            return _agent_path(path)
        agent = _agent_path(path)
        if agent == "/":
            return self._container_prefix
        return f"{self._container_prefix}{agent}"

    def _map_out(self, inner_path: str) -> str:
        normalized = inner_path.replace("\\", "/")
        if self._container_prefix is None:
            return _agent_path(normalized)
        prefix = self._container_prefix
        if normalized == prefix:
            return "/"
        if normalized.startswith(f"{prefix}/"):
            return _agent_path(normalized[len(prefix) :])
        return _agent_path(normalized)

    def _normalize_file_info(self, entry: FileInfo) -> FileInfo:
        raw_path = entry["path"]
        is_dir = bool(entry.get("is_dir"))
        rel = self._map_out(raw_path.rstrip("/") if is_dir else raw_path)
        if is_dir:
            rel = f"{rel.rstrip('/')}/"
        return {**entry, "path": rel}

    def _normalize_grep_match(self, match: GrepMatch) -> GrepMatch:
        return {**match, "path": self._map_out(match["path"])}

    def ls(self, path: str) -> LsResult:
        result = self._inner.ls(self._map_in(path))
        if result.error or not result.entries:
            return result
        return LsResult(entries=[self._normalize_file_info(entry) for entry in result.entries])

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._inner.read(self._map_in(file_path), offset=offset, limit=limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        if self._read_only:
            return WriteResult(error=_READ_ONLY_SKILLS_ERROR)
        return self._inner.write(self._map_in(file_path), content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        if self._read_only:
            return EditResult(error=_READ_ONLY_SKILLS_ERROR)
        return self._inner.edit(
            self._map_in(file_path),
            old_string,
            new_string,
            replace_all=replace_all,
        )

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        if path is None:
            mapped = self._container_prefix if self._container_prefix is not None else path
        else:
            mapped = self._map_in(path)
        result = self._inner.grep(pattern, path=mapped, glob=glob)
        if result.error or not result.matches:
            return result
        return GrepResult(matches=[self._normalize_grep_match(m) for m in result.matches])

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        result = self._inner.glob(pattern, path=self._map_in(path))
        if result.error or not result.matches:
            return result
        return GlobResult(matches=[self._normalize_file_info(m) for m in result.matches])

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        if self._read_only:
            return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]
        mapped = [(self._map_in(path), content) for path, content in files]
        responses = self._inner.upload_files(mapped)
        return [
            FileUploadResponse(path=agent_path, error=responses[i].error if i < len(responses) else None)
            for i, (agent_path, _) in enumerate(files)
        ]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        mapped = [self._map_in(path) for path in paths]
        responses = self._inner.download_files(mapped)
        return [
            FileDownloadResponse(
                path=agent_path,
                content=responses[i].content if i < len(responses) else None,
                error=responses[i].error if i < len(responses) else None,
            )
            for i, agent_path in enumerate(paths)
        ]

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        if not isinstance(self._inner, SandboxBackendProtocol):
            msg = "Inner backend does not support command execution"
            raise NotImplementedError(msg)
        # 禁止对 command 做 shlex round-trip rewrite（会破坏 > | && 等 Shell 操作符）。
        # Agent 应使用相对路径或容器真实挂载路径（/workspace、/skills/public|personal）。
        return self._inner.execute(command, timeout=timeout)

    @property
    def id(self) -> str:
        if isinstance(self._inner, SandboxBackendProtocol):
            return self._inner.id
        return f"prefix:{id(self)}"
