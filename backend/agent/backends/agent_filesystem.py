"""Agent 虚拟路径：`/research/` 工作区 + `/skills/extensions|custom/` 只读 Skills。"""

from __future__ import annotations

from pathlib import Path

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)

from agent.backends.aio_sandbox import AioSandboxBackend
from agent.backends.local_shell import create_local_shell_backend
from agent.backends.mount_paths import (
    AGENT_CUSTOM_SKILLS_ROUTE,
    AGENT_EXTENSIONS_SKILLS_ROUTE,
    CUSTOM_SKILLS_CONTAINER_PREFIX,
    EXTENSIONS_SKILLS_CONTAINER_PREFIX,
)
from config.extensions_paths import skills_root
from config.user_data_paths import get_user_skills_dir, get_workspace_dir

_READ_ONLY_SKILLS_ERROR = "Skills directory is read-only"


def _agent_path(path: str) -> str:
    return path if path.startswith("/") else f"/{path}"


class PrefixBackend(SandboxBackendProtocol):
    """将 Composite 剥前缀后的 agent 路径映射到 inner backend（可选容器绝对前缀）。"""

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

    def ls(self, path: str) -> LsResult:
        return self._inner.ls(self._map_in(path))

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
        return self._inner.grep(pattern, path=mapped, glob=glob)

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        if path == "/" and self._container_prefix is not None:
            mapped = path
        else:
            mapped = self._map_in(path)
        return self._inner.glob(pattern, path=mapped)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        if self._read_only:
            return [
                FileUploadResponse(path=path, error="permission_denied") for path, _ in files
            ]
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
        return self._inner.execute(command, timeout=timeout)

    @property
    def id(self) -> str:
        if isinstance(self._inner, SandboxBackendProtocol):
            return self._inner.id
        return f"prefix:{id(self)}"


def _skills_route_backend(
    *,
    sandbox: AioSandboxBackend | None,
    host_root: Path,
    container_prefix: str,
    shell_timeout: int,
) -> PrefixBackend:
    if sandbox is not None:
        inner: BackendProtocol = sandbox
        return PrefixBackend(
            inner,
            container_prefix=container_prefix,
            read_only=True,
        )
    return PrefixBackend(
        create_local_shell_backend(
            host_root,
            virtual_mode=True,
            timeout=shell_timeout,
        ),
        read_only=True,
    )


def build_agent_filesystem_backend(
    *,
    user_id: str,
    session_id: str,
    sandbox: AioSandboxBackend | None,
    shell_timeout: int,
) -> CompositeBackend:
    """构建 Agent 文件系统：default=工作区，routes=extensions/custom skills（只读）。"""
    extensions_root = skills_root()
    custom_root = get_user_skills_dir(user_id)

    if sandbox is not None:
        workspace_inner: SandboxBackendProtocol = sandbox
        workspace = PrefixBackend(
            workspace_inner,
            container_prefix=f"/workspace/sessions/{session_id}/workspace",
        )
    else:
        session_ws = get_workspace_dir(user_id, session_id)
        workspace = PrefixBackend(
            create_local_shell_backend(
                session_ws,
                virtual_mode=True,
                timeout=shell_timeout,
            ),
        )

    extensions = _skills_route_backend(
        sandbox=sandbox,
        host_root=extensions_root,
        container_prefix=EXTENSIONS_SKILLS_CONTAINER_PREFIX,
        shell_timeout=shell_timeout,
    )
    custom = _skills_route_backend(
        sandbox=sandbox,
        host_root=custom_root,
        container_prefix=CUSTOM_SKILLS_CONTAINER_PREFIX,
        shell_timeout=shell_timeout,
    )

    return CompositeBackend(
        default=workspace,
        routes={
            AGENT_EXTENSIONS_SKILLS_ROUTE: extensions,
            AGENT_CUSTOM_SKILLS_ROUTE: custom,
        },
    )
