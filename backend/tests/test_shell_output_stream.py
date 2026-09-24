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
