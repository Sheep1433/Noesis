"""Configure passwordless SSH login by installing the local public key on a remote host."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Dict, Optional

from config import get_config, resolved_ssh_dir
from executor import (
    build_sshpass_command,
    build_ssh_command,
    classify_exec_failure,
    classify_ssh_stderr,
    exec_local,
)
from utils.errors import CommandExecutionError, InternalError, SSHAuthFailedError
from utils.output_handler import success_result

_PUBKEY_CANDIDATES = ("id_ed25519.pub", "id_rsa.pub", "id_ecdsa.pub")


def _require_ip(ip: str) -> None:
    if not ip:
        raise InternalError("ip is required")


def _resolve_public_key(explicit: Optional[str]) -> Path:
    cfg = get_config()
    ssh_dir = Path(resolved_ssh_dir(cfg))
    if explicit:
        key = Path(explicit).expanduser()
        if not key.is_file():
            raise InternalError(f"Public key not found: {explicit}")
        return key
    for name in _PUBKEY_CANDIDATES:
        candidate = ssh_dir / name
        if candidate.is_file():
            return candidate
    raise InternalError(
        f"No SSH public key found in {ssh_dir}. "
        "On the MCP host run: ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519"
    )


def _fail_install(cmd: list[str], exit_code: int, stdout: str, stderr: str) -> None:
    err = classify_exec_failure(cmd, stderr)
    if err is not None:
        raise err
    raise CommandExecutionError(f"Failed to install public key: {stderr or stdout or exit_code}")


def setup_passwordless_login(
    ip: str,
    password: str,
    username: str = "root",
    port: Optional[int] = None,
    public_key_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Install the local SSH public key on a remote host for passwordless login.

    Uses password authentication once, then verifies key-only login works.
    Requires sshpass on the MCP host (for one-time password bootstrap).
    """
    _require_ip(ip)
    if not password:
        raise InternalError("password is required for initial setup")

    cfg = get_config()
    user = username or cfg.ssh.default_user
    effective_port = port if port is not None else cfg.ssh.default_port
    pubkey_path = _resolve_public_key(public_key_path)
    pubkey = pubkey_path.read_text(encoding="utf-8").strip()

    remote_install = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        f"grep -qxF {shlex.quote(pubkey)} ~/.ssh/authorized_keys 2>/dev/null || "
        f"echo {shlex.quote(pubkey)} >> ~/.ssh/authorized_keys && "
        "chmod 600 ~/.ssh/authorized_keys"
    )

    install_cmd = build_sshpass_command(
        ip, user, effective_port, password, remote_install
    )
    exit_code, stdout, stderr = exec_local(install_cmd, timeout=60)
    if exit_code != 0:
        _fail_install(install_cmd, exit_code, stdout, stderr)

    verify_cmd = build_ssh_command(
        ip, user, effective_port, "echo __SSH_OK__", batch_mode=True
    )
    exit_code, stdout, stderr = exec_local(verify_cmd, timeout=30)
    if exit_code != 0 or "__SSH_OK__" not in stdout:
        err = classify_ssh_stderr(stderr)
        if err is not None:
            raise err
        raise SSHAuthFailedError(f"Passwordless login verification failed: {stderr or stdout}")

    return success_result({
        "host": ip,
        "username": user,
        "port": effective_port,
        "public_key": str(pubkey_path),
        "verified": True,
    })
