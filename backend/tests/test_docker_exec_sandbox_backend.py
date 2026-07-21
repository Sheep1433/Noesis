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
        self.reason_phrase = "error"

    def json(self) -> dict:
        return self._payload


class _FakeHttpClient:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []
        self.puts: list[tuple[str, dict]] = []
        self._exec_status = 200

    def post(self, url: str, *, headers: dict, json: dict) -> _FakeResponse:
        self.posts.append((url, json))
        if url.endswith("/exec"):
            return _FakeResponse(
                status_code=self._exec_status,
                payload={"output": "ok\n", "exit_code": 0, "truncated": False},
                text="missing" if self._exec_status == 404 else "",
            )
        if url.endswith("/files/write"):
            return _FakeResponse()
        if url.endswith("/files/read"):
            return _FakeResponse(payload={"content": "hello", "encoding": None})
        raise AssertionError(f"unexpected url: {url}")

    def put(self, url: str, *, headers: dict, json: dict) -> _FakeResponse:
        self.puts.append((url, json))
        return _FakeResponse(payload={"runtime": "docker", "container_name": "c1"})


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
    assert url.endswith("/internal/sandboxes/u1/sessions/s1/exec")
    assert payload["command"] == "echo hi"
    assert payload["exec_dir"] == "/workspace"


def test_execute_preserves_shell_operators(backend: DockerExecSandboxBackend) -> None:
    cmd = "mkdir -p out && printf done > out/result.txt"
    backend.execute(cmd)
    _, payload = backend._http.posts[0]
    assert payload["command"] == cmd
    assert "'>'" not in payload["command"]
    assert "'&&'" not in payload["command"]


def test_execute_404_rebuilds_and_retries(backend: DockerExecSandboxBackend) -> None:
    client = backend._http
    client._exec_status = 404

    def _post(url: str, *, headers: dict, json: dict) -> _FakeResponse:
        client.posts.append((url, json))
        if url.endswith("/exec"):
            # first call 404, after put succeed
            status = 404 if len([p for p in client.posts if p[0].endswith("/exec")]) == 1 else 200
            return _FakeResponse(
                status_code=status,
                payload={"output": "ok\n", "exit_code": 0, "truncated": False},
                text="missing" if status == 404 else "",
            )
        raise AssertionError(url)

    client.post = _post  # type: ignore[method-assign]
    # _ensure_sync uses httpx.Client().put — patch via backend method
    backend._ensure_sync = lambda: None  # type: ignore[method-assign]
    result = backend.execute("echo hi")
    assert result.exit_code == 0
    assert len([p for p in client.posts if p[0].endswith("/exec")]) == 2


def test_upload_writes_utf8_via_runner(backend: DockerExecSandboxBackend) -> None:
    path = "/workspace/research/plan.md"
    markdown = "# 研究规划\n\n中文正文"
    result = backend.upload_files([(path, markdown.encode("utf-8"))])
    assert result[0].error is None
    _, payload = backend._http.posts[-1]
    assert payload["file"] == path
    assert payload["content"] == markdown
    assert "encoding" not in payload


def test_download_reads_runner_payload(backend: DockerExecSandboxBackend) -> None:
    path = "/workspace/notes.md"
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
