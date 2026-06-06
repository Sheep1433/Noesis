from __future__ import annotations

from typing import Optional

from config import get_config
from utils.output_handler import CommandResult


def build_ssh_command(host: str, user: str, port: int, command: str) -> list[str]:
    """Build an ssh command as a list (safe, no shell injection)."""
    cfg = get_config()
    return [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", f"ConnectTimeout={cfg.execution.connect_timeout}",
        "-o", "LogLevel=ERROR",
        "-p", str(port),
        f"{user}@{host}",
        command,
    ]


def build_ssh_batch_command(host: str, user: str, port: int, command: str) -> list[str]:
    """Build ssh command that requires key-based auth (for post-setup verification)."""
    cfg = get_config()
    return [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "PasswordAuthentication=no",
        "-o", "StrictHostKeyChecking=no",
        "-o", f"ConnectTimeout={cfg.execution.connect_timeout}",
        "-o", "LogLevel=ERROR",
        "-p", str(port),
        f"{user}@{host}",
        command,
    ]


def exec_in_container(cmd: list[str], timeout: Optional[int] = None) -> tuple[int, str, str]:
    """Execute a command inside the sandbox container. Returns (exit_code, stdout, stderr)."""
    from docker_manager import DockerManager  # 延迟导入避免循环引用
    dm = DockerManager()
    return dm.exec_in_container(cmd, timeout=timeout)


def exec_ssh(host: str, user: str, port: int, command: str, timeout: Optional[int] = None) -> CommandResult:
    """
    Execute a command on a remote host via ssh.
    The ssh command runs inside the Docker container (which has the ssh client).
    """
    if timeout is None:
        timeout = get_config().execution.timeout

    ssh_cmd = build_ssh_command(host, user, port, command)
    exit_code, stdout, stderr = exec_in_container(ssh_cmd, timeout=timeout)
    return CommandResult(stdout=stdout, stderr=stderr, exit_code=exit_code)
