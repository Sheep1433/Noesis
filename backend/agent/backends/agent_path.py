"""Agent 路径适配：canonicalize + 可选 strip（local FS）/ 只读。"""

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

from agent.backends.paths import (
    READ_ONLY_SKILLS_ERROR,
    canonicalize_agent_path,
    join_prefix,
    strip_prefix,
)


def _abs(path: str) -> str:
    return path if path.startswith("/") else f"/{path}"


class AgentPathBackend(SandboxBackendProtocol):
    """统一入口：可选 canonicalize；local 时 strip ``/workspace`` 给 FilesystemBackend。"""

    def __init__(
        self,
        inner: BackendProtocol,
        *,
        strip_root: str | None = None,
        read_only: bool = False,
        canonicalize: bool = True,
    ) -> None:
        self._inner = inner
        self._strip_root = strip_root.rstrip("/") if strip_root else None
        self._read_only = read_only
        self._canonicalize = canonicalize

    def _map_in(self, path: str) -> str:
        agent = canonicalize_agent_path(path) if self._canonicalize else _abs(path)
        if self._strip_root is None:
            return agent
        return strip_prefix(agent, self._strip_root)

    def _map_out(self, inner_path: str) -> str:
        normalized = inner_path.replace("\\", "/")
        if self._strip_root is None:
            return _abs(normalized)
        return join_prefix(normalized, self._strip_root)

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
            return WriteResult(error=READ_ONLY_SKILLS_ERROR)
        return self._inner.write(self._map_in(file_path), content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        if self._read_only:
            return EditResult(error=READ_ONLY_SKILLS_ERROR)
        return self._inner.edit(
            self._map_in(file_path),
            old_string,
            new_string,
            replace_all=replace_all,
        )

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        if path is None:
            mapped = None
            if self._strip_root is not None:
                mapped = "/"
            elif self._canonicalize:
                mapped = canonicalize_agent_path("/")
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
            raise NotImplementedError("Inner backend does not support command execution")
        return self._inner.execute(command, timeout=timeout)

    @property
    def id(self) -> str:
        if isinstance(self._inner, SandboxBackendProtocol):
            return self._inner.id
        return f"agent-path:{id(self)}"
