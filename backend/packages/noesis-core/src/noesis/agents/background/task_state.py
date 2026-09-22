"""任务身份在父 Agent graph state 的投影契约（两个 kind 的工具面共用）。

工具面（subagent/tools.py 的 start/update、shell/tools.py 的 execute 后台
分支）启动任务成功后经 ``Command`` 把身份写入 graph state ``async_tasks``
键，随 checkpoint 持久化、免疫上下文压缩。state 是投影——任务状态与结果
的权威来源永远是运行时注册表（miss 落 DB），``check_async_task`` 不信快照。
"""
from __future__ import annotations

import time
from typing import Annotated, Any, NotRequired, TypedDict

from langchain.agents.middleware.types import AgentState


class AsyncTask(TypedDict):
    """任务身份投影（上游同构字段 + description），压缩后模型仍可恢复任务清单。

    与上游的对应：thread_id = 子会话公开身份（child session），agent_name =
    角色类型（后台命令为 shell）。时间戳为写入时刻值，不随后续操作刷新
    ——权威状态实时查运行时，state 只服务压缩后的任务清单重建。
    """

    task_id: str
    agent_name: str
    thread_id: str
    run_id: str
    status: str
    description: str
    created_at: str
    last_checked_at: str
    last_updated_at: str


def _merge_async_tasks(
    existing: dict[str, AsyncTask] | None,
    update: dict[str, AsyncTask],
) -> dict[str, AsyncTask]:
    """按 task_id 合并；终态条目保留（压缩后已收结果的任务仍可追溯）。"""
    merged = dict(existing or {})
    merged.update(update)
    return merged


class SubagentTasksState(AgentState):
    async_tasks: NotRequired[Annotated[dict[str, AsyncTask], _merge_async_tasks]]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def build_async_task_identity(task: dict[str, Any]) -> AsyncTask:
    """从运行时任务快照构造 state 身份条目（start/update/execute 后台分支共用）。

    时间戳为写入时刻值：check/update 不回写 state（权威状态实时查运行时），
    字段保留上游 AsyncTask 形状。
    """
    now = _now_iso()
    return AsyncTask(
        task_id=str(task["task_id"]),
        agent_name=str(task.get("subagent_type") or "shell"),
        thread_id=str(task.get("child_session_id") or task["task_id"]),
        run_id=str(task.get("run_id") or ""),
        status=str(task.get("status") or ""),
        description=str(task.get("description") or ""),
        created_at=now,
        last_checked_at=now,
        last_updated_at=now,
    )


__all__ = [
    "AsyncTask",
    "SubagentTasksState",
    "build_async_task_identity",
]
