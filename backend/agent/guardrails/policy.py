"""SuperAgent HITL 审批谓词：memory 写入与网络/pipe execute。"""

from __future__ import annotations

import re
from typing import Any

from agent.backends.paths import AGENT_MEMORY_ROUTE, canonicalize_agent_path

# 少问策略：仅外联（网络出口 / pipe-to-shell）；workspace 内破坏性命令不审批
_PIPE_TO_SHELL = re.compile(r"\|\s*(?:ba)?sh\b", re.IGNORECASE)
_NETWORK_CMDS = re.compile(
    r"(?:^|[;&|]\s*|\b)"
    r"(?:"
    r"curl|wget|ssh|scp|nc|ncat|netcat|"
    r"git\s+push|"
    r"pip(?:3)?\s+install|"
    r"npm\s+install|"
    r"pnpm\s+(?:i|install|add)|"
    r"yarn\s+add"
    r")\b",
    re.IGNORECASE,
)


def is_memory_write_path(path: str | None) -> bool:
    """目标路径规范化后是否以 ``/memory/`` 为前缀。"""
    raw = (path or "").strip()
    if not raw:
        return False
    try:
        normalized = canonicalize_agent_path(raw)
    except ValueError:
        return False
    prefix = AGENT_MEMORY_ROUTE.rstrip("/") + "/"
    return normalized == AGENT_MEMORY_ROUTE.rstrip("/") or normalized.startswith(prefix)


def is_network_execute(command: str | None) -> bool:
    """是否匹配网络出口类命令（含 curl|sh 管道）。"""
    cmd = (command or "").strip()
    if not cmd:
        return False
    if _PIPE_TO_SHELL.search(cmd):
        return True
    return bool(_NETWORK_CMDS.search(cmd))


def is_dangerous_execute(command: str | None) -> bool:
    """需审批的 execute：仅网络/pipe（沙箱内 rm 等默认放行）。"""
    return is_network_execute(command)


def memory_write_when(req: Any) -> bool:
    """``InterruptOnConfig.when``：write_file / edit_file 指向 memory 时 interrupt。"""
    args = (getattr(req, "tool_call", None) or {}).get("args") or {}
    path = args.get("path") or args.get("file_path") or ""
    return is_memory_write_path(str(path))


def execute_when(req: Any, *, session_id: str | None = None) -> bool:
    """``InterruptOnConfig.when``：网络类 execute；可被 session grant 跳过。"""
    from agent.guardrails.session_grants import session_grants

    args = (getattr(req, "tool_call", None) or {}).get("args") or {}
    command = str(args.get("command") or "")
    if not is_dangerous_execute(command):
        return False
    if session_id and is_network_execute(command) and session_grants.has_network_grant(session_id):
        return False
    return True
