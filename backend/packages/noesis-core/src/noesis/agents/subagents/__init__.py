"""进程内后台任务运行时（subagent / shell 双 kind）与子 Agent 角色注册表。"""

from noesis.agents.subagents.executor import (
    BackgroundTaskExecutor,
    BackgroundTask,
    BgTaskStatus,
    fail_session_shell_tasks,
    shutdown,
    shutdown_loop,
)
from noesis.agents.subagents.notify_middleware import BgNotifyMiddleware
from noesis.agents.subagents.registry import (
    BG_TASK_TOOL_NAMES,
    SubagentRegistry,
    SubagentRole,
    assert_no_bg_task_tools,
)
from noesis.agents.subagents.tools_middleware import NoesisSubagentMiddleware

__all__ = [
    "BG_TASK_TOOL_NAMES",
    "BackgroundTaskExecutor",
    "BackgroundTask",
    "BgNotifyMiddleware",
    "BgTaskStatus",
    "NoesisSubagentMiddleware",
    "SubagentRegistry",
    "SubagentRole",
    "assert_no_bg_task_tools",
    "fail_session_shell_tasks",
    "shutdown",
    "shutdown_loop",
]
