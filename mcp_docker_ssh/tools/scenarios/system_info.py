"""
system_info: 查看远程主机的 CPU / 内存 / 磁盘 / 进程信息
"""

from __future__ import annotations

from typing import Any, Dict

from utils.output_handler import get_result_data, success_result

from ..core import bash


def system_info(
    ip: str,
    username: str = "root",
) -> Dict[str, Any]:
    """Gather system overview (hostname, uptime, CPU, memory, disk, top processes)."""
    commands = [
        ("hostname", "echo '=== Hostname ===' && hostname"),
        ("uptime", "echo '=== Uptime ===' && uptime"),
        ("cpu", "echo '=== CPU Info ===' && cat /proc/cpuinfo | grep 'model name' | head -1"),
        ("memory", "echo '=== Memory ===' && free -h"),
        ("disk", "echo '=== Disk ===' && df -h | grep -v 'tmpfs'"),
        ("top_processes", "echo '=== Top Processes ===' && ps aux --sort=-%cpu | head -11"),
    ]

    sections: Dict[str, str] = {}
    for key, cmd in commands:
        result = bash(cmd, ip=ip, username=username)
        sections[key] = get_result_data(result)["stdout"].strip()

    return success_result({"sections": sections})
