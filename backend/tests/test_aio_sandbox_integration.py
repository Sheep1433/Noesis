"""AIO 沙箱集成测试（需 Docker + sandbox-runner，默认 skip）。"""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_same_user_two_sessions_one_container() -> None:
    """集成：同用户两 session 复用一容器（需 Docker + runner）。"""
    pytest.skip("需 Docker + sandbox-runner + AIO 镜像")


@pytest.mark.integration
def test_execute_may_read_other_session_workspace() -> None:
    """集成：同用户 execute 可读 /workspace/sessions/s2/...。"""
    pytest.skip("需 Docker + sandbox-runner + AIO 镜像")
