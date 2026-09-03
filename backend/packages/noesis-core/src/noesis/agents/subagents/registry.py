"""子 Agent 角色注册表：类型名 → worker 编译配方声明。

角色（SubagentRole）是类型分发的唯一声明面：name 供模型在 ``start_task``
的 ``subagent_type`` 选择，worker_factory 闭包捕获该类型的系统提示词、
工具集与 backend 策略，model_id 为配置层模型绑定（未绑定沿用父 Agent
模型）。注册发生在 SuperAgent 装配期，重名即失败；执行器不感知类型——
启动前由调用方把角色解析为 worker_factory 注入。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

# 后台任务工具名集：任何角色的 worker 工具集都不得携带（禁止递归委派）。
# 防线在装配期集中断言，而非依赖各角色配方自觉剔除。
BG_TASK_TOOL_NAMES = frozenset({
    "start_task", "check_task", "cancel_task", "list_tasks", "send_message",
})


@dataclass(frozen=True)
class SubagentRole:
    """一个子 Agent 角色的不可变声明。"""

    name: str
    # 供模型选择角色的行为描述（注入 system prompt 类型清单）
    description: str
    # worker 编译工厂（async，隔离 loop 内惰性调用）；入参为模型覆盖，
    # 角色绑定模型时调用方应以绑定模型为基线传覆盖
    worker_factory: Callable[[Optional[str]], Any]
    # 审批配置；缺省沿用装配层统一的 interrupt_on
    interrupt_on: Optional[dict] = None
    # 配置层模型绑定；None = 沿用父 Agent 模型。start_task 不暴露模型
    # 参数——运行时只选角色，模型在配置层按角色解析
    model_id: Optional[str] = None


def assert_no_bg_task_tools(worker_tools: list) -> None:
    """装配期断言 worker 工具集不含后台任务工具（递归委派防线前移）。"""
    names = {getattr(tool, "name", "") for tool in worker_tools}
    overlap = names & BG_TASK_TOOL_NAMES
    if overlap:
        raise ValueError(f"worker 工具集不得包含后台任务工具：{sorted(overlap)}")


class SubagentRegistry:
    """进程内角色注册表；装配期构建，一次装配一份实例。"""

    def __init__(self) -> None:
        self._roles: dict[str, SubagentRole] = {}

    def register(self, role: SubagentRole) -> None:
        if role.name in self._roles:
            raise ValueError(f"子 Agent 角色重名：{role.name}")
        self._roles[role.name] = role

    def get(self, name: str) -> Optional[SubagentRole]:
        return self._roles.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(self._roles)

    def types_prompt(self) -> str:
        """类型清单（逐行 ``- name: description``），注入 system prompt。"""
        return "\n".join(f"- {r.name}: {r.description}" for r in self._roles.values())

    def effective_model(self, name: str, parent_model_id: Optional[str]) -> Optional[str]:
        """解析角色的生效模型：绑定值优先，未绑定沿用父 Agent 模型。"""
        role = self._roles.get(name)
        if role is None:
            return parent_model_id
        return role.model_id or parent_model_id


__all__ = [
    "BG_TASK_TOOL_NAMES",
    "SubagentRegistry",
    "SubagentRole",
    "assert_no_bg_task_tools",
]
