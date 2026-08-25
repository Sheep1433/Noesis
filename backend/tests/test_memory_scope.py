from __future__ import annotations

import subprocess
from pathlib import Path

from noesis.config import user_data_paths
from noesis.services.memory.scope import build_scope_key, normalize_git_remote, resolve_project_key


def test_remote_identity_ignores_credentials_transport_and_dot_git() -> None:
    assert normalize_git_remote("git@Example.com:Org/Repo.git") == "example.com/org/repo"
    assert normalize_git_remote("https://token@example.com/Org/Repo.git") == "example.com/org/repo"


def test_project_key_uses_remote_origin_or_global(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(user_data_paths, "_USERS_ROOT", tmp_path / "users")
    workspace = user_data_paths.ensure_workspace_dir("u1", "s1")
    assert resolve_project_key("u1", "s1") == "global"

    subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)
    assert resolve_project_key("u1", "s1") == "global"

    subprocess.run(
        ["git", "-C", str(workspace), "remote", "add", "origin", "git@example.com:Org/Repo.git"],
        check=True,
        capture_output=True,
    )
    remote = resolve_project_key("u1", "s1")
    assert remote.startswith("git-origin:")
    assert build_scope_key(agent_profile="SUPER_AGENT_QA", project_key=remote).startswith(
        "profile:SUPER_AGENT_QA|project:git-origin:"
    )


def test_originless_sandbox_repo_shares_global_scope_across_sessions(
    tmp_path: Path, monkeypatch
) -> None:
    """沙箱内 git init 且无 origin：不得产生仅本会话可复现的死胡同 scope。"""
    monkeypatch.setattr(user_data_paths, "_USERS_ROOT", tmp_path / "users")
    first = user_data_paths.ensure_workspace_dir("u1", "session-a")
    second = user_data_paths.ensure_workspace_dir("u1", "session-b")
    for workspace in (first, second):
        subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)

    assert resolve_project_key("u1", "session-a") == "global"
    assert resolve_project_key("u1", "session-b") == "global"
