"""shell 任务运行中输出流（output_tail）回归。

钉住四件事：命令包装（重定向 + 退出码保留）、本地日志尾部读取与截断、
check_async_task 对 running shell 任务附输出尾部快照、subagent 任务不误附。
"""

from __future__ import annotations

from types import SimpleNamespace

from noesis.agents.background.shell.kernel import (
    _read_log_tail,
    _wrap_command_for_log,
)
from noesis.agents.background.subagent.tools import _format_task


def test_wrap_command_redirects_and_preserves_exit_code() -> None:
    wrapped = _wrap_command_for_log("echo hello", "bg-1")
    assert "mkdir -p .task-outputs" in wrapped
    assert "( echo hello )" in wrapped
    assert "> .task-outputs/bg-1.log 2>&1" in wrapped
    # 子 shell 包裹：命令自身的退出码就是 wrapped 的退出码（无管道吞码）
    assert "PIPESTATUS" not in wrapped


def test_wrap_command_multiline_command() -> None:
    wrapped = _wrap_command_for_log("echo a\necho b", "bg-m")
    assert "( echo a\necho b )" in wrapped
    assert ".task-outputs/bg-m.log" in wrapped


def test_read_log_tail_local_reads_tail(tmp_path, monkeypatch) -> None:
    log = tmp_path / "ws" / ".task-outputs" / "bg-1.log"
    log.parent.mkdir(parents=True)
    log.write_text("line-1\nline-2\nline-3\n", encoding="utf-8")
    entry = SimpleNamespace(
        task=SimpleNamespace(user_id="u1", session_id="s1"),
        shell_backend=SimpleNamespace(cwd=tmp_path / "ws"),  # 本地分支
    )
    tail = _read_log_tail(entry, ".task-outputs/bg-1.log", 4096)
    assert "line-3" in tail


def test_read_log_tail_truncates_to_max_chars(tmp_path, monkeypatch) -> None:
    log = tmp_path / "ws" / ".task-outputs" / "bg-2.log"
    log.parent.mkdir(parents=True)
    log.write_text("x" * 100, encoding="utf-8")
    entry = SimpleNamespace(
        task=SimpleNamespace(user_id="u1", session_id="s1"),
        shell_backend=SimpleNamespace(cwd=tmp_path / "ws"),
    )
    tail = _read_log_tail(entry, ".task-outputs/bg-2.log", 10)
    assert len(tail) == 10


def test_check_async_task_running_shell_appends_output_tail() -> None:
    """running shell 任务：hint 后附输出尾部快照（模型可中途看进度）。"""
    task = {
        "task_id": "bg-1",
        "child_session_id": None,
        "kind": "shell",
        "status": "running",
        "description": "长命令",
        "output_tail": "line-41\nline-42",
    }
    text = _format_task(task)
    assert "仍在运行" in text
    assert "[运行中输出尾部]" in text
    assert "line-42" in text


def test_check_async_task_subagent_running_has_no_output_tail() -> None:
    task = {
        "task_id": "bg-2",
        "child_session_id": "child-1",
        "kind": "subagent",
        "status": "running",
        "description": "调研",
        "output_tail": None,
    }
    text = _format_task(task)
    assert "运行中输出尾部" not in text


def test_wrap_command_empty_uses_noop_placeholder() -> None:
    """空命令以 : 占位——(  ) 是语法错误（退出码 2），旧行为退出码 0。"""
    wrapped = _wrap_command_for_log("", "bg-empty")
    assert "( : )" in wrapped


def test_read_log_tail_docker_uses_exec_tail(monkeypatch) -> None:
    """docker 分支：经 runner exec API 跑 tail（有界读、文本输出），
    不走 file-read API（其 base64 编码会把非 UTF-8 日志变乱码）。"""
    import httpx as real_httpx

    calls: list[dict] = []

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"output": "line-41\nline-42\n", "exit_code": 0, "truncated": False}

    class _FakeClient:
        def __init__(self, **_kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, **kw):
            calls.append({"url": url, "json": kw.get("json")})
            return _FakeResponse()

    from noesis.agents.backends.docker_exec import DockerExecSandboxBackend

    class _DockerLike(DockerExecSandboxBackend):
        """不调 __init__（绕开 runner 连接），仅承载 isinstance 分支。"""

    backend = _DockerLike.__new__(_DockerLike)  # 跳过 __init__：无 runner 状态
    entry = SimpleNamespace(
        task=SimpleNamespace(user_id="u1", session_id="s1"),
        shell_backend=backend,
    )


    # kernel 函数体内 from noesis.config.env import SandboxConfig——
    # env.SandboxConfig 是冻结实例，patch 整个名字
    import noesis.config.env as env_mod

    monkeypatch.setattr(env_mod, "SandboxConfig", SimpleNamespace(runner_url="http://runner:8090"))
    monkeypatch.setattr(env_mod, "sandbox_runner_headers", lambda: {})
    monkeypatch.setattr(real_httpx, "Client", _FakeClient)

    tail = _read_log_tail(entry, ".task-outputs/bg-3.log", 4096)
    assert "line-42" in tail
    assert len(calls) == 1
    sent = calls[0]["json"]
    assert sent["command"].startswith("tail -c ")
    assert "/workspace/.task-outputs/bg-3.log" in sent["command"]
    assert sent["exec_dir"] == "/workspace"
