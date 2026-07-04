"""宿主机 subprocess 执行 ssh / sshpass。"""

from __future__ import annotations

import subprocess
from typing import Optional

from config import get_config
from utils.errors import (
    CommandTimeoutError,
    InternalError,
    SSHAuthFailedError,
    SSHAuthRequiredError,
    SSHConnectionFailedError,
)
from utils.output_handler import CommandResult, OutputHandler

_output_handler = OutputHandler()


def _ssh_cli_options(*, batch_mode: bool = False) -> list[str]:
    cfg = get_config()
    options = [
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        f"ConnectTimeout={cfg.execution.connect_timeout}",
        "-o",
        "LogLevel=ERROR",
    ]
    if batch_mode:
        options = [
            "-o",
            "BatchMode=yes",
            "-o",
            "PasswordAuthentication=no",
            *options,
        ]
    return options


def build_ssh_command(
    host: str,
    user: str,
    port: int,
    command: str,
    *,
    batch_mode: bool = False,
) -> list[str]:
    return [
        "ssh",
        *_ssh_cli_options(batch_mode=batch_mode),
        "-p",
        str(port),
        f"{user}@{host}",
        command,
    ]


def build_sshpass_command(
    host: str,
    user: str,
    port: int,
    password: str,
    remote_command: str,
) -> list[str]:
    return [
        "sshpass",
        "-p",
        password,
        "ssh",
        *_ssh_cli_options(),
        "-p",
        str(port),
        f"{user}@{host}",
        remote_command,
    ]


def classify_ssh_stderr(stderr: str) -> Optional[Exception]:
    """Map OpenSSH client stderr to typed MCP errors."""
    if not stderr:
        return None
    text = stderr.strip()
    lower = text.lower()
    if not lower.startswith("ssh:"):
        if "permission denied (publickey" in lower and "password" in lower:
            return SSHAuthRequiredError(text)
        return None

    if "permission denied (publickey" in lower and "password" in lower:
        return SSHAuthRequiredError(text)
    if "permission denied" in lower:
        return SSHAuthFailedError(text)
    if any(kw in lower for kw in ("connection refused", "could not resolve hostname", "no route to host")):
        return SSHConnectionFailedError(text)
    if "connection timed out" in lower or "operation timed out" in lower:
        return SSHConnectionFailedError(text)
    return None


def classify_local_failure(cmd: list[str], stderr: str) -> Optional[Exception]:
    if not stderr:
        return None
    lower = stderr.lower()
    if "no such file or directory" not in lower:
        return None
    binary = cmd[0] if cmd else ""
    if binary == "ssh":
        return InternalError("ssh not found on MCP host; install openssh-client")
    if binary == "sshpass":
        return InternalError("sshpass not found on MCP host (e.g. brew install sshpass)")
    return None


def classify_exec_failure(cmd: list[str], stderr: str) -> Optional[Exception]:
    return classify_ssh_stderr(stderr) or classify_local_failure(cmd, stderr)


def exec_local(cmd: list[str], timeout: Optional[int] = None) -> tuple[int, str, str]:
    """在 MCP 宿主机执行本地命令。返回 (exit_code, stdout, stderr)。"""
    if timeout is None:
        timeout = get_config().execution.timeout
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CommandTimeoutError(f"Command timed out after {timeout}s") from exc
    except OSError as exc:
        binary = cmd[0] if cmd else "command"
        if binary == "ssh":
            raise InternalError("ssh not found on MCP host; install openssh-client") from exc
        if binary == "sshpass":
            raise InternalError("sshpass not found on MCP host (e.g. brew install sshpass)") from exc
        raise InternalError(f"Failed to run {binary}: {exc}") from exc

    stdout_str = (completed.stdout or b"").decode("utf-8", errors="replace")
    stderr_str = (completed.stderr or b"").decode("utf-8", errors="replace")
    result = _output_handler.truncate_result(
        CommandResult(
            stdout=stdout_str,
            stderr=stderr_str,
            exit_code=completed.returncode,
        )
    )
    return result.exit_code, result.stdout, result.stderr


def exec_remote(
    host: str,
    user: str,
    port: int,
    command: str,
    timeout: Optional[int] = None,
) -> CommandResult:
    """经宿主机 ssh 在远程执行命令；失败时抛出类型化 MCP 错误。"""
    if timeout is None:
        timeout = get_config().execution.timeout

    ssh_cmd = build_ssh_command(host, user, port, command)
    exit_code, stdout, stderr = exec_local(ssh_cmd, timeout=timeout)
    result = CommandResult(stdout=stdout, stderr=stderr, exit_code=exit_code)
    if exit_code != 0:
        err = classify_exec_failure(ssh_cmd, stderr)
        if err is not None:
            raise err
    return result
