"""进程内后台子 Agent（全异步 task + HITL 审批续跑）。"""

from noesis.agents.subagents.executor import (
    BackgroundSubagentExecutor,
    BackgroundTask,
    BgTaskStatus,
    configure_task_store,
    shutdown,
    shutdown_loop,
)
from noesis.agents.subagents.notify_middleware import BgNotifyMiddleware
from noesis.agents.subagents.tools import build_background_task_tools

__all__ = [
    "BackgroundSubagentExecutor",
    "BackgroundTask",
    "BgTaskStatus",
    "BgNotifyMiddleware",
    "configure_task_store",
    "build_background_task_tools",
    "shutdown",
    "shutdown_loop",
]
