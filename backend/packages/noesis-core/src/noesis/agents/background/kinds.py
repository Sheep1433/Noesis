"""TaskKindRuntime：一种后台任务 kind 的行为差异面。

运行时（executor）对 kind 的全部认知收敛为：能力字段读取 + 本协议的
方法调用——运行时代码中不出现任何 ``kind ==`` 判断，新增任务类型 =
新增一个行为对象实现。行为对象必须是无状态单例：任务私有状态全部在
``_TaskEntry`` 的 kind 专属字段里，行为对象不得持有任务集合。

锁纪律（契约的一部分，review 与测试依据）：

- ``request_stop`` / ``on_timeout_locked`` 在 ``_TASKS_LOCK`` 内被调用，
  实现不得再取锁、不得做任何 I/O 或 await；
- ``run`` 构造执行协程（调度点在锁内调用它，构造须为纯同步、无 I/O），
  返回的协程仅在隔离事件循环上执行，绝不持锁运行。
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from noesis.agents.background.jobs.registry import _TaskEntry


class StopMode(Enum):
    """停止模式：运行时据此走既有两条停止路径。"""

    # 置协作停止信号，静止边界退出，宽限超时硬杀（乐观终态受理）
    COOPERATIVE = "cooperative"
    # 直接 cancel 执行 future，即时终态（命令在 backend 不可中断）
    IMMEDIATE_CANCEL = "immediate"


@runtime_checkable
class TaskKindRuntime(Protocol):
    """一种 kind 的能力声明 + 行为注入点。"""

    kind: str
    # 能否追加消息（False：锁内即拒绝，文案由 reject_append_text 提供）
    supports_message_append: bool
    # 通知负载是否携带 turn_count
    has_turns: bool

    def reject_append_text(self) -> str:
        """追加消息拒绝文案（含替代指引）。"""
        ...

    def run(self, entry: "_TaskEntry") -> Any:
        """执行内核：返回隔离 loop 上可调度的协程工厂入参。"""
        ...

    def request_stop(self, entry: "_TaskEntry") -> StopMode:
        """停止模式选择（锁内，禁 I/O）。"""
        ...

    def on_timeout_locked(self, entry: "_TaskEntry") -> bool:
        """超时计划（锁内，禁 I/O）：True = 硬杀，False = 协作 timed_out。"""
        ...


def behavior_of(kind: str) -> "TaskKindRuntime":
    """按 kind 取行为对象；未注册大声失败。"""
    if not KIND_BEHAVIORS:
        _register_builtin_kinds()
    behavior = KIND_BEHAVIORS.get(kind)
    if behavior is None:
        raise ValueError(f"未注册的后台任务类型：{kind}")
    return behavior


# kind → 行为对象注册表（新增任务类型在此注册，运行时零 kind 判断）。
# 内置 kind 惰性注册：kinds 与两个内核互相依赖（内核 import StopMode），
# 顶层导入依导入顺序崩坏，故在 behavior_of 首次调用时填充
KIND_BEHAVIORS: "dict[str, TaskKindRuntime]" = {}


def _register_builtin_kinds() -> None:
    from noesis.agents.background.subagent.kernel import _SubagentKind
    from noesis.agents.background.shell.kernel import _ShellKind

    KIND_BEHAVIORS.update(
        {"subagent": _SubagentKind(), "shell": _ShellKind()}
    )


__all__ = ["KIND_BEHAVIORS", "StopMode", "TaskKindRuntime", "behavior_of"]
