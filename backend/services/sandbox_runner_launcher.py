"""本地开发时自动拉起 sandbox-runner（aio 模式、runner 未运行时）。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

from agent.backends import uses_aio_sandbox
from common.logging import logger
from common.paths import REPO_ROOT
from config.env import SandboxConfig

_RUNNER_DIR = REPO_ROOT / "deploy" / "sandbox-runner"
_PROC: subprocess.Popen[bytes] | None = None


def _runner_healthy() -> bool:
    url = f"{SandboxConfig.runner_url.rstrip('/')}/health"
    try:
        with httpx.Client(timeout=2.0) as client:
            return client.get(url).status_code == 200
    except httpx.HTTPError:
        return False


def _spawn_runner() -> subprocess.Popen[bytes] | None:
    main_py = _RUNNER_DIR / "main.py"
    if not main_py.is_file():
        logger.warning("未找到 sandbox-runner: %s", main_py)
        return None

    uv = shutil.which("uv")
    if uv:
        cmd = [uv, "run", "python", "main.py"]
    else:
        cmd = [sys.executable, str(main_py)]

    return subprocess.Popen(
        cmd,
        cwd=str(_RUNNER_DIR),
        env=os.environ.copy(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def ensure_sandbox_runner_process() -> None:
    """`uv run app.py` 直接启动时确保 runner 可用（已由 run.sh 拉起则跳过）。"""
    global _PROC

    if os.environ.get("SANDBOX_RUNNER_AUTOSTART", "1").lower() in ("0", "false", "no"):
        return
    if not uses_aio_sandbox():
        return
    if _runner_healthy():
        return
    if shutil.which("docker") is None:
        logger.warning("Docker 未安装，无法自动启动 sandbox-runner")
        return

    _PROC = _spawn_runner()
    if _PROC is None:
        return

    for _ in range(30):
        if _runner_healthy():
            logger.info("sandbox-runner 已自动启动 (%s)", SandboxConfig.runner_url)
            return
        time.sleep(1)

    logger.warning("sandbox-runner 自动启动超时 (%s)", SandboxConfig.runner_url)
