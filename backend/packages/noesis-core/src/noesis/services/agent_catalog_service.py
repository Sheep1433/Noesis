"""会话 Agent 目录与 shell job 用例。

API 不直接访问 executor。对话型 child 的权威状态来自 ChatSession/AgentRun；
executor 这里只提供运行中的 shell job 摘要和停止操作。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.agents.subagents.executor import (
    BackgroundTaskExecutor,
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
                "interrupt": runtime.get("interrupt") or child.get("interrupt"),
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
        return {
            "tasks": tasks,
            "pending_approvals": [
                task for task in tasks if task.get("status") == "awaiting_approval"
            ],
        }

    @staticmethod
    def subscribe(session_id: str, user_id: str):
        return subscribe_bg_events(session_id, user_id)

    @staticmethod
    def unsubscribe(session_id: str, queue) -> None:
        unsubscribe_bg_events(session_id, queue)


class ShellJobService:
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
