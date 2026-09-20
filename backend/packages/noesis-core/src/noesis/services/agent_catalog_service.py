"""会话 Agent 目录与 shell job 用例。

API 不直接访问 executor。对话型 child 的权威状态来自 ChatSession/AgentRun；
executor 这里只提供运行中的 shell job 摘要和停止操作。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.agents.background.executor import BackgroundTaskExecutor
from noesis.agents.background.jobs.events import (
    subscribe_bg_events,
    unsubscribe_bg_events,
)
from noesis.errors.exceptions import ServiceException
from noesis.services.chat_service import ChatService


class AgentCatalogService:
    @classmethod
    async def list_for_session(
        cls,
        session_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> dict[str, list[dict[str, Any]]]:
        runtime_tasks = [
            task
            for task in BackgroundTaskExecutor.list_for_session(session_id)
            if str(task.get("user_id") or user_id) == str(user_id)
        ]
        catalog = await ChatService.get_child_session_catalog(
            parent_id=session_id,
            user_id=user_id,
            db=db,
        )
        runtime_by_child = {
            str(task.get("child_session_id")): task
            for task in runtime_tasks
            if task.get("kind") == "subagent" and task.get("child_session_id")
        }
        tasks: list[dict[str, Any]] = []
        for child in catalog:
            runtime = runtime_by_child.get(str(child["session_id"])) or {}
            tasks.append({
                # UI/API 只暴露 child session；executor task id 是内部实现细节。
                "task_id": str(child["session_id"]),
                "child_session_id": child["session_id"],
                "created_by_tool_call_id": child.get("created_by_tool_call_id"),
                "session_id": session_id,
                "description": child["title"],
                "kind": "subagent",
                "status": runtime.get("status") or child["status"],
                "result": runtime.get("result"),
                "error": runtime.get("error"),
                "run_id": child.get("run_id"),
                "started_at": runtime.get("started_at") or child.get("started_at"),
                "completed_at": runtime.get("completed_at") or child.get("finished_at"),
                "progress_count": runtime.get("progress_count") or child.get("step_count", 0),
                "turn_count": runtime.get("turn_count") or child.get("turn_count", 0),
            })
        tasks.extend(
            {key: value for key, value in task.items() if key != "user_id"}
            for task in runtime_tasks
            if task.get("kind") == "shell"
        )
        # shell 目录 DB 兜底：热集 miss（终态回收 / 重启后）的 shell 任务
        # 从 bg_shell_job 行补齐，消除「凭空消失」
        from noesis.agents.background.ports import ShellJobPort

        shell_rows = await ShellJobPort.list_for_session(session_id)
        known_shell = {str(task.get("task_id")) for task in tasks if task.get("kind") == "shell"}
        tasks.extend(
            row for row in shell_rows if str(row.get("task_id")) not in known_shell
        )
        return {"tasks": tasks}

    @staticmethod
    def subscribe(session_id: str, user_id: str):
        return subscribe_bg_events(session_id, user_id)

    @staticmethod
    def unsubscribe(session_id: str, queue) -> None:
        unsubscribe_bg_events(session_id, queue)


class ShellJobService:
    @staticmethod
    async def get_task_status(*, session_id: str, task_id: str, user_id: str) -> dict[str, Any] | None:
        """只读任务快照（command 提交后的响应组装用）；不存在返回 None。

        热集 miss 回退 bg_shell_job 行（终态回收 / 重启后仍可答）。
        """
        tasks = BackgroundTaskExecutor.list_for_session(session_id)
        memory_hit = next(
            (
                item for item in tasks
                if item.get("task_id") == task_id
                and item.get("kind") == "shell"
                and str(item.get("user_id") or user_id) == str(user_id)
            ),
            None,
        )
        if memory_hit is not None:
            return memory_hit
        from noesis.agents.background.ports import ShellJobPort

        row = await ShellJobPort.get_task(task_id)
        if row is None or str(row.get("session_id")) != str(session_id) or str(row.get("user_id")) != str(user_id):
            return None
        return {key: value for key, value in row.items() if key != "user_id"}

    @staticmethod
    def stop(*, session_id: str, task_id: str, user_id: str) -> dict[str, Any]:
        tasks = BackgroundTaskExecutor.list_for_session(session_id)
        task = next(
            (
                item for item in tasks
                if item.get("task_id") == task_id
                and item.get("kind") == "shell"
                and str(item.get("user_id") or user_id) == str(user_id)
            ),
            None,
        )
        if task is None:
            raise ServiceException(message="后台命令不存在")
        try:
            return BackgroundTaskExecutor.cancel(task_id)
        except ValueError as exc:
            raise ServiceException(message=str(exc)) from exc


__all__ = ["AgentCatalogService", "ShellJobService"]
