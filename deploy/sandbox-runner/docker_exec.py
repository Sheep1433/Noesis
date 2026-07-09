"""容器内 shell / 文件操作（docker SDK，供 runner 内网 API 调用）。"""

from __future__ import annotations

import base64
import io
import os
import tarfile
import time
from dataclasses import dataclass

from docker.errors import APIError, NotFound
from docker.models.containers import Container
_MAX_OUTPUT_BYTES = 512 * 1024


@dataclass(frozen=True)
class ExecResult:
    output: str
    exit_code: int
    truncated: bool


@dataclass(frozen=True)
class ReadFileResult:
    content: bytes
    encoding: str | None = None


def _combine_output(stdout: bytes | None, stderr: bytes | None) -> tuple[str, bool]:
    parts: list[bytes] = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(stderr)
    raw = b"".join(parts)
    truncated = len(raw) > _MAX_OUTPUT_BYTES
    if truncated:
        raw = raw[:_MAX_OUTPUT_BYTES]
    return raw.decode("utf-8", errors="replace"), truncated


def exec_command(
    container: Container,
    *,
    command: str,
    exec_dir: str | None = None,
    timeout: float | None = None,
) -> ExecResult:
    """在容器内执行单条 bash 命令（无跨调用 shell 状态）。"""
    workdir = exec_dir or "/workspace"
    api = container.client.api
    if timeout is not None and timeout > 0:
        shell_cmd = f"timeout --signal=TERM {int(timeout)} {command}"
    else:
        shell_cmd = command
    exec_id = api.exec_create(
        container.id,
        cmd=["/bin/bash", "-lc", shell_cmd],
        workdir=workdir,
        stdout=True,
        stderr=True,
    )
    try:
        stdout, stderr = api.exec_start(exec_id, demux=True, stream=False)
    except APIError as exc:
        return ExecResult(output=f"docker exec failed: {exc}", exit_code=1, truncated=False)
    inspect = api.exec_inspect(exec_id)
    exit_code = int(inspect.get("ExitCode", 1) or 0)
    output, truncated = _combine_output(stdout, stderr)
    return ExecResult(output=output, exit_code=exit_code, truncated=truncated)


def _mkdir_p(container: Container, directory: str) -> None:
    exec_command(container, command=f"mkdir -p {directory!r}", exec_dir="/")


def read_file_bytes(container: Container, *, path: str) -> ReadFileResult:
    try:
        stream, _stat = container.get_archive(path)
    except NotFound as exc:
        raise FileNotFoundError(path) from exc
    except APIError as exc:
        if exc.status_code == 404:
            raise FileNotFoundError(path) from exc
        if "is a directory" in str(exc).lower():
            raise IsADirectoryError(path) from exc
        raise

    payload = io.BytesIO()
    for chunk in stream:
        payload.write(chunk)
    payload.seek(0)
    with tarfile.open(fileobj=payload, mode="r:*") as tar:
        members = tar.getmembers()
        if not members:
            raise FileNotFoundError(path)
        member = members[0]
        extracted = tar.extractfile(member)
        if extracted is None:
            raise FileNotFoundError(path)
        content = extracted.read()
    return ReadFileResult(content=content)


def write_file_bytes(container: Container, *, path: str, content: bytes) -> None:
    parent = os.path.dirname(path) or "/"
    name = os.path.basename(path)
    if not name:
        raise ValueError(f"invalid file path: {path!r}")
    _mkdir_p(container, parent)

    tarstream = io.BytesIO()
    with tarfile.open(fileobj=tarstream, mode="w") as tar:
        info = tarfile.TarInfo(name=name)
        info.size = len(content)
        info.mode = 0o644
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(content))
    tarstream.seek(0)
    try:
        container.put_archive(parent, tarstream.getvalue())
    except APIError as exc:
        raise PermissionError(str(exc)) from exc


def read_file_text(container: Container, *, path: str) -> tuple[str, str | None]:
    result = read_file_bytes(container, path=path)
    try:
        return result.content.decode("utf-8"), None
    except UnicodeDecodeError:
        return base64.b64encode(result.content).decode("ascii"), "base64"


def write_file_text(
    container: Container,
    *,
    path: str,
    content: str,
    encoding: str | None = None,
) -> None:
    if encoding == "base64":
        payload = base64.b64decode(content)
    else:
        payload = content.encode("utf-8")
    write_file_bytes(container, path=path, content=payload)
