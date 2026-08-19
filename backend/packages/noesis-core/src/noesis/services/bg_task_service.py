"""后台子 Agent 的查询与审批用例。

API 层入口：会话后台任务列表 / 待审批列表 / 审批决策。执行面全部委托
BackgroundSubagentExecutor（进程内注册表）；本 Service 只做权限归属校验
与载荷翻译，不持有自己的状态。
"""

from __future__ import annotations

from typing import Any

from noesis.agents.subagents.executor import (
    BackgroundSubagentExecutor,
    BgTaskStatus,
)
from noesis.errors.exceptions import NotFoundException
from noesis.runtime.logging import logger


class BgTaskService:
    @classmethod
    def list_for_session(cls, session_id: str, user_id: str) -> list[dict[str, Any]]:
        tasks = BackgroundSubagentExecutor.list_for_session(session_id)
        return [t for t in tasks if t.get("user_id") == user_id or not t.get("user_id")]

    @classmethod
    def pending_approvals(cls, session_id: str, user_id: str) -> list[dict[str, Any]]:
        return [
            t for t in cls.list_for_session(session_id, user_id)
            if t.get("status") == BgTaskStatus.AWAITING_APPROVAL.value
        ]

    @classmethod
    def get_task(cls, task_id: str, user_id: str) -> dict[str, Any]:
        task = BackgroundSubagentExecutor.get(task_id)
        if task is None:
            raise NotFoundException(f"后台任务不存在: {task_id}")
        if task.get("user_id") not in (None, user_id):
            raise NotFoundException(f"后台任务不存在: {task_id}")
        return task

    @classmethod
    def submit_decisions(
        cls, task_id: str, user_id: str, decisions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        cls.get_task(task_id, user_id)  # 归属校验 + 404 语义
        try:
            snapshot = BackgroundSubagentExecutor.submit_decisions(task_id, decisions)
        except ValueError as exc:
            raise NotFoundException(str(exc)) from exc
        logger.info(
            "bg task decisions submitted task_id={} decisions={}",
            task_id, [d.get("type") for d in decisions],
        )
        return snapshot

    @classmethod
    def get_messages(cls, task_id: str, user_id: str) -> list[dict[str, Any]]:
        """子会话查看：只读该任务 thread 的完整消息历史。"""
        cls.get_task(task_id, user_id)
        try:
            return BackgroundSubagentExecutor.read_messages(task_id)
        except ValueError as exc:
            raise NotFoundException(str(exc)) from exc

    @classmethod
    def send_message(cls, task_id: str, user_id: str, message: str) -> dict[str, Any]:
        cls.get_task(task_id, user_id)
        try:
            return BackgroundSubagentExecutor.send_message(task_id, message)
        except ValueError as exc:
            raise NotFoundException(str(exc)) from exc

    @classmethod
    def cancel(cls, task_id: str, user_id: str) -> dict[str, Any]:
        cls.get_task(task_id, user_id)
        try:
            return BackgroundSubagentExecutor.cancel(task_id)
        except ValueError as exc:
            raise NotFoundException(str(exc)) from exc
