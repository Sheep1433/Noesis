"""后台任务子系统（subagent / shell 双 kind）的稳定导入面。

分层规则——顶层放跨 kind 共享物，kind 专属物进子包：

- jobs/          共享运行时（状态机/隔离循环/登记表/事件/终态收口）
- subagent/      subagent kind（turn 链内核 kernel + 角色注册表 roles + 工具面 tools）
- shell/         shell kind（命令内核 kernel + execute 工具面 tools）
- task_state.py  两 kind 工具面共用的任务身份 graph state 投影契约
- executor.py    门面（任务台 CRUD + shutdown 编排）
- kinds.py       kind 行为协议与注册表
- 其余顶层文件    通知（notifications/notify_middleware）

命名纪律：本 __init__ 的 ``__all__`` 是对外的全部稳定契约（装配与服务层
消费这些名字）；其余名字——含各模块的下划线名——属包内实现，跨模块协作
与测试可以直接引用，但改动不承诺兼容。新增对外能力先在此登记。
"""

from noesis.agents.background.executor import (
    BackgroundTaskExecutor,
    BackgroundTask,
    BgTaskStatus,
    shutdown,
)
from noesis.agents.background.notify_middleware import BgNotifyMiddleware
from noesis.agents.background.subagent.roles import (
    SubagentRegistry,
    SubagentRole,
    assert_no_bg_task_tools,
)
from noesis.agents.background.shell.kernel import fail_session_shell_tasks
from noesis.agents.background.subagent.tools import AsyncSubagentToolsMiddleware

__all__ = [
    "AsyncSubagentToolsMiddleware",
    "BackgroundTask",
    "BackgroundTaskExecutor",
    "BgNotifyMiddleware",
    "BgTaskStatus",
    "SubagentRegistry",
    "SubagentRole",
    "assert_no_bg_task_tools",
    "fail_session_shell_tasks",
    "shutdown",
]
