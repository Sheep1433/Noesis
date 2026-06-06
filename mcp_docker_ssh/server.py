"""
MCP Docker SSH server - FastMCP 入口，工具注册（L1 原子工具 + L2 场景工具）
通过 Docker 容器内 ssh 命令执行远程操作（免密登录，见 setup_passwordless_login）。
"""

from __future__ import annotations

import sys
from typing import Any, Dict, Literal, Optional

from fastmcp import FastMCP
from loguru import logger

logger.remove()
logger.add(sys.stderr, level="WARNING", format="{time:HH:mm:ss} {level} {message}", colorize=True)

from tools.core import read as _read, grep as _grep, glob as _glob, bash as _bash
from tools.ssh_setup import setup_passwordless_login as _setup_passwordless_login
from tools.scenarios import system_info as _system_info_scenario
from tools.scenarios import playbook_log as _playbook_log_scenario

GrepOutputMode = Literal["content", "files_with_matches", "count"]

mcp = FastMCP("docker-ssh")


@mcp.tool()
def setup_passwordless_login(
    ip: str,
    password: str,
    username: str = "root",
    port: Optional[int] = None,
    public_key_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Configure passwordless SSH login to a remote host (one-time password bootstrap).

    Installs the local public key into remote ~/.ssh/authorized_keys and verifies key auth.

    Args:
        ip: Remote host IP address (required).
        password: Remote account password (used once for setup).
        username: SSH username (default "root").
        port: SSH port (default 22).
        public_key_path: Path to local public key (default: auto-detect id_ed25519.pub / id_rsa.pub).
    """
    logger.info(f"setup_passwordless_login: ip={ip} user={username}")
    return _setup_passwordless_login(
        ip=ip,
        password=password,
        username=username,
        port=port,
        public_key_path=public_key_path,
    )


@mcp.tool()
def read(
    path: str,
    ip: str,
    offset: int = 1,
    limit: Optional[int] = None,
    username: str = "root",
) -> Dict[str, Any]:
    """
    Read text file content from a remote host over SSH (text files only).

    Args:
        path: Absolute path to the text file on the remote host.
        ip: Remote host IP address (required).
        offset: 1-indexed starting line (default 1).
        limit: Maximum lines to read (optional).
        username: SSH username (default "root").
    """
    logger.info(f"read: ip={ip} path={path}")
    return _read(path=path, ip=ip, offset=offset, limit=limit, username=username)


@mcp.tool()
def grep(
    pattern: str,
    ip: str,
    path: str = ".",
    glob: Optional[str] = None,
    output_mode: GrepOutputMode = "files_with_matches",
    context_before: Optional[int] = None,
    context_after: Optional[int] = None,
    context: Optional[int] = None,
    context_c: Optional[int] = None,
    show_line_numbers: bool = True,
    ignore_case: bool = False,
    file_type: Optional[str] = None,
    head_limit: Optional[int] = None,
    offset: int = 0,
    multiline: bool = False,
    username: str = "root",
) -> Dict[str, Any]:
    """
    Search file contents on a remote host using regex.

    Args:
        pattern: Regular expression pattern.
        ip: Remote host IP address (required).
        path: Search root path (default ".").
        glob: Glob filter for file names (e.g. "*.py").
        output_mode: content | files_with_matches (default) | count.
        context_before: Lines before match (-B); content mode only.
        context_after: Lines after match (-A); content mode only.
        context: Lines before and after match; content mode only.
        context_c: Alias for context (-C); content mode only.
        show_line_numbers: Show line numbers (-n); content mode, default true.
        ignore_case: Case insensitive search (-i).
        file_type: File type filter (py/js/ts/rust/go/java...).
        head_limit: Max results (default 250); 0 = unlimited.
        offset: Skip first N entries before head_limit.
        multiline: Enable multiline regex mode.
        username: SSH username (default "root").
    """
    logger.info(f"grep: ip={ip} pattern={pattern} path={path} mode={output_mode}")
    return _grep(
        pattern=pattern,
        ip=ip,
        path=path,
        glob=glob,
        output_mode=output_mode,
        context_before=context_before,
        context_after=context_after,
        context=context,
        context_c=context_c,
        show_line_numbers=show_line_numbers,
        ignore_case=ignore_case,
        file_type=file_type,
        head_limit=head_limit,
        offset=offset,
        multiline=multiline,
        username=username,
    )


@mcp.tool()
def glob(
    pattern: str,
    ip: str,
    path: str = ".",
    username: str = "root",
) -> Dict[str, Any]:
    """
    Match files by glob pattern on a remote host (max 100 files).

    Args:
        pattern: Glob pattern (e.g. "*.log").
        ip: Remote host IP address (required).
        path: Search root path (default ".").
        username: SSH username (default "root").
    """
    logger.info(f"glob: ip={ip} pattern={pattern} path={path}")
    return _glob(pattern=pattern, ip=ip, path=path, username=username)


@mcp.tool()
def bash(
    command: str,
    ip: str,
    timeout_ms: Optional[int] = None,
    username: str = "root",
) -> Dict[str, Any]:
    """
    Execute a shell command on a remote host over SSH.

    Args:
        command: Shell command string to execute.
        ip: Remote host IP address (required).
        timeout_ms: Timeout in milliseconds (default 120000).
        username: SSH username (default "root").
    """
    logger.info(f"bash: ip={ip} command={command[:60]}")
    return _bash(command=command, ip=ip, timeout_ms=timeout_ms, username=username)


@mcp.tool()
def system_info(
    ip: str,
    username: str = "root",
) -> Dict[str, Any]:
    """
    Gather system overview (hostname, uptime, CPU, memory, disk, top processes).

    Args:
        ip: Remote host IP address (required).
        username: SSH username (default "root").
    """
    logger.info(f"system_info: ip={ip}")
    return _system_info_scenario(ip=ip, username=username)


@mcp.tool()
def playbook_log(
    ip: str,
    limit: int = 100,
    errors_only: bool = True,
    username: str = "root",
) -> Dict[str, Any]:
    """
    Find and read the latest Ansible playbook log, optionally filtering for errors.

    Args:
        ip: Remote host IP address (required).
        limit: Max lines to read (default 100).
        errors_only: If True, only return failed/error lines (default True).
        username: SSH username (default "root").
    """
    logger.info(f"playbook_log: ip={ip}")
    return _playbook_log_scenario(ip=ip, limit=limit, errors_only=errors_only, username=username)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="MCP Docker SSH Server")
    parser.add_argument("--transport", default="http", choices=["stdio", "http"], help="Transport type")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP bind host (only for http transport)")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (only for http transport)")
    args = parser.parse_args()

    if args.transport == "http":
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run()
