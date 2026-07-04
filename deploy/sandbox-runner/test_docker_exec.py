"""docker_exec 单元测试（mock 容器 API）。"""

from __future__ import annotations

import io
import tarfile
from unittest.mock import MagicMock

import pytest

from docker_exec import exec_command, read_file_text, write_file_text


def _make_container(*, exec_result: tuple[bytes, bytes] | None = None) -> MagicMock:
    container = MagicMock()
    api = MagicMock()
    container.id = "cid"
    container.client.api = api
    if exec_result is None:
        exec_result = (b"hello\n", b"")
    api.exec_create.return_value = "exec-id"
    api.exec_start.return_value = exec_result
    api.exec_inspect.return_value = {"ExitCode": 0}
    return container


def test_exec_command_runs_bash_with_workdir() -> None:
    container = _make_container()
    result = exec_command(container, command="echo hi", exec_dir="/workspace/s1")
    assert result.exit_code == 0
    assert "hello" in result.output
    api = container.client.api
    api.exec_create.assert_called_once()
    kwargs = api.exec_create.call_args.kwargs
    assert kwargs["workdir"] == "/workspace/s1"
    assert kwargs["cmd"] == ["/bin/bash", "-lc", "echo hi"]


def test_exec_command_wraps_timeout() -> None:
    container = _make_container()
    exec_command(container, command="sleep 99", timeout=30)
    kwargs = container.client.api.exec_create.call_args.kwargs
    assert kwargs["cmd"] == ["/bin/bash", "-lc", "timeout --signal=TERM 30 sleep 99"]


def test_write_and_read_file_roundtrip() -> None:
    container = _make_container()
    archive_store: dict[str, bytes] = {}

    def put_archive(parent: str, data: bytes) -> None:
        archive_store[parent] = data

    def get_archive(path: str):
        parent = path.rsplit("/", 1)[0] or "/"
        name = path.rsplit("/", 1)[-1]
        raw = archive_store.get(parent)
        if raw is None:
            from docker.errors import NotFound

            raise NotFound("missing")
        stream = io.BytesIO(raw)
        return stream, {"name": name}

    container.put_archive.side_effect = put_archive
    container.get_archive.side_effect = get_archive

    write_file_text(container, path="/workspace/s1/notes.md", content="你好")
    text, encoding = read_file_text(container, path="/workspace/s1/notes.md")
    assert encoding is None
    assert text == "你好"
