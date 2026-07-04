"""DockerExecSandboxBackend：mock runner HTTP、mutex 与路径校验。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest

from agent.backends.docker_exec_sandbox import DockerExecSandboxBackend


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload


class _FakeHttpClient:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    def post(self, url: str, *, headers: dict, json: dict) -> _FakeResponse:
        self.posts.append((url, json))
        if url.endswith("/exec"):
            return _FakeResponse(payload={"output": "ok\n", "exit_code": 0, "truncated": False})
        if url.endswith("/files/write"):
            return _FakeResponse()
        if url.endswith("/files/read"):
            return _FakeResponse(payload={"content": "hello", "encoding": None})
        raise AssertionError(f"unexpected url: {url}")


@pytest.fixture
def backend() -> DockerExecSandboxBackend:
    return DockerExecSandboxBackend(
        user_id="u1",
        session_id="s1",
        http_client=_FakeHttpClient(),  # type: ignore[arg-type]
    )


def test_execute_calls_runner_exec(backend: DockerExecSandboxBackend) -> None:
    backend.execute("echo hi")
    client = backend._http
    assert len(client.posts) == 1
    url, payload = client.posts[0]
    assert url.endswith("/internal/sandboxes/u1/exec")
    assert payload["command"] == "echo hi"
    assert payload["exec_dir"] == "/workspace/sessions/s1/workspace"


def test_upload_writes_utf8_via_runner(backend: DockerExecSandboxBackend) -> None:
    path = "/workspace/sessions/s1/workspace/research/plan.md"
    markdown = "# 研究规划\n\n中文正文"
    result = backend.upload_files([(path, markdown.encode("utf-8"))])
    assert result[0].error is None
    _, payload = backend._http.posts[-1]
    assert payload["file"] == path
    assert payload["content"] == markdown
    assert "encoding" not in payload


def test_download_reads_runner_payload(backend: DockerExecSandboxBackend) -> None:
    path = "/workspace/sessions/s1/workspace/notes.md"
    result = backend.download_files([path])
    assert result[0].error is None
    assert result[0].content == b"hello"


def test_execute_http_error_returns_failure() -> None:
    client = MagicMock(spec=httpx.Client)
    client.post.side_effect = httpx.ConnectError("refused")
    backend = DockerExecSandboxBackend(
        user_id="u1",
        session_id="s1",
        http_client=client,
    )
    result = backend.execute("echo hi")
    assert result.exit_code == 1
    assert "failed" in result.output.lower()
