"""task-worker 的 /memory 只读路由与 HITL 变体契约。"""

from noesis.agents.backends.factory import (
    WORKER_MEMORY_READ_ONLY_ERROR,
    build_agent_filesystem_backend,
)
from noesis.agents.backends.paths import AGENT_MEMORY_ROUTE
from noesis.agents.tools.ask_user import build_interrupt_on


def test_worker_interrupt_omits_memory_write_guard() -> None:
    """worker 的 /memory 在 backend 层只读：无需（也不应）再挂写入审批。"""
    worker = build_interrupt_on(session_id="s", memory_write_guard=False)
    main = build_interrupt_on(session_id="s")

    assert "write_file" not in worker and "edit_file" not in worker
    assert "execute" in worker and "ask_user" in worker
    assert "write_file" in main and "edit_file" in main


def test_worker_memory_route_is_read_only() -> None:
    """memory_read_only=True 时 /memory 写入被拒并给出指引文案。"""
    from noesis.agents.backends.agent_path import AgentPathBackend
    user_id = "u-readonly"
    backend = build_agent_filesystem_backend(
        user_id=user_id,
        session_id="s-readonly",
        sandbox=None,
        shell_timeout=5,
        memory_read_only=True,
    )
    memory_route = backend.routes[AGENT_MEMORY_ROUTE]
    assert isinstance(memory_route, AgentPathBackend)  # 已包只读壳
    result = memory_route.write("/memory/USER.md", "x")
    assert result.error == WORKER_MEMORY_READ_ONLY_ERROR
    # 读路径不受影响
    read = memory_route.read("/memory/USER.md")
    assert read.error != WORKER_MEMORY_READ_ONLY_ERROR
    # 默认（主 Agent）组装保持可写：路由对象不经只读包装
    writable = build_agent_filesystem_backend(
        user_id=user_id,
        session_id="s-readonly",
        sandbox=None,
        shell_timeout=5,
    )
    assert not isinstance(writable.routes[AGENT_MEMORY_ROUTE], AgentPathBackend)
