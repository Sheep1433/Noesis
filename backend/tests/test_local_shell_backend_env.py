"""Agent LocalShellBackend 环境：PATH / 密钥过滤 / gh 可发现性。"""

from __future__ import annotations

import os
import shutil

import pytest

from agent.backends.local_shell import (
    _merge_path,
    build_shell_execute_env,
    create_local_shell_backend,
)


def test_merge_path_puts_homebrew_before_system() -> None:
    merged = _merge_path("/custom/bin")
    parts = merged.split(":")
    assert parts[0] == "/opt/homebrew/bin"
    assert "/usr/bin" in parts
    assert parts[-1] == "/custom/bin"


def test_build_shell_execute_env_strips_secrets_keeps_gh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_API_KEY", "sk-secret")
    monkeypatch.setenv("GH_TOKEN", "gho_test")
    monkeypatch.setenv("MY_APP_SETTING", "ok")

    env = build_shell_execute_env()

    assert "MODEL_API_KEY" not in env
    assert env.get("GH_TOKEN") == "gho_test"
    assert env.get("MY_APP_SETTING") == "ok"
    assert "/opt/homebrew/bin" in env["PATH"].split(":")


def test_create_local_shell_backend_can_resolve_gh(tmp_path) -> None:
    if shutil.which("gh") is None:
        pytest.skip("gh not installed on host")

    workspace = tmp_path / "agent_workspace"
    workspace.mkdir()
    backend = create_local_shell_backend(workspace, virtual_mode=True)
    result = backend.execute("gh --version")

    assert result.exit_code == 0
    assert "gh version" in result.output


def test_create_local_shell_backend_https_curl_returns_body(tmp_path) -> None:
    """验证 shell backend 能通过 curl 正常发起 HTTPS 请求。"""
    workspace = tmp_path / "agent_workspace"
    workspace.mkdir()
    backend = create_local_shell_backend(workspace, virtual_mode=True)
    result = backend.execute(
        'curl -sS "https://httpbin.org/get?foo=bar"'
    )

    assert result.exit_code == 0
    assert "bar" in result.output
    assert "httpbin" in result.output
