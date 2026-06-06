"""
playbook_log: 查看 Ansible playbook 运行日志
组合 glob + read + grep 工具。
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Dict, List

from utils.output_handler import get_result_data, success_result

from ..core import bash, glob, read


def _split_glob_pattern(pattern: str) -> tuple[str, str]:
    """Split '/var/log/ansible/*.log' into search path and file pattern."""
    posix = PurePosixPath(pattern)
    parent = posix.parent
    if str(parent) in (".", ""):
        return ".", posix.name
    return str(parent), posix.name


def playbook_log(
    ip: str,
    limit: int = 100,
    errors_only: bool = True,
    username: str = "root",
) -> Dict[str, Any]:
    """
    Find and read the latest Ansible playbook log, optionally filtering for errors.
    """
    log_patterns = ["/var/log/ansible/*.log", "~/ansible/*.log", "/root/ansible/*.log"]
    log_files: List[str] = []

    for pattern in log_patterns:
        search_path, file_pattern = _split_glob_pattern(pattern)
        result = glob(file_pattern, ip=ip, path=search_path, username=username)
        files = get_result_data(result)["filenames"]
        if files:
            log_files.extend(files)

    if not log_files:
        fallback = bash(
            "ls -t ~/ansible/*.log /var/log/ansible/*.log 2>/dev/null | head -1",
            ip=ip,
            username=username,
        )
        fallback_content = get_result_data(fallback)["stdout"].strip()
        if fallback_content:
            log_files = [fallback_content]

    if not log_files:
        return success_result({
            "log_file": "",
            "content": "No ansible log files found",
            "lines_read": 0,
        }, empty=True)

    latest_log = log_files[0]
    read_result = read(
        latest_log,
        ip=ip,
        offset=max(1, limit - 99),
        limit=limit,
        username=username,
    )
    content = get_result_data(read_result)["content"]

    lines_read = len(content.splitlines())
    if errors_only:
        error_lines = [
            line for line in content.splitlines()
            if re.search(r"\bfailed\b|\berror\b", line, re.IGNORECASE)
        ]
        return success_result({
            "log_file": latest_log,
            "errors": error_lines,
            "total_errors": len(error_lines),
        }, empty=len(error_lines) == 0)

    return success_result({
        "log_file": latest_log,
        "content": content,
        "lines_read": lines_read,
    })
