"""子 Agent 执行器与会话用例之间的窄端口。

运行器和产品服务都依赖这里的协议，不相互 import。应用启动时由各自模块注册
实现，避免 executor ↔ service 的循环依赖，同时保持运行时调用是显式的。
"""

from __future__ import annotations

from typing import Any

_SERVICE: Any = None
_EXECUTOR: Any = None


def child_session_summary(task: dict, *, parent_id: str) -> dict:
    """child 会话目录摘要的单一构造点（纯函数，无服务依赖）。

    三处共用（executor 事件推送 / 目录快照 / 目录事件流）：输入为
    BackgroundTask.to_dict() 形状的 task dict。放端口模块——运行器与
    产品服务共同依赖的中立位置，执行器热路径不依赖服务注册状态。
    """
    return {
        "session_id": task.get("child_session_id") or task.get("task_id"),
        "parent_id": parent_id,
        "title": task.get("description") or "子 Agent",
        "profile_id": "task-worker",
        "created_by_tool_call_id": task.get("created_by_tool_call_id"),
        "run_id": task.get("run_id"),
        "status": task.get("status"),
        "turn_count": task.get("turn_count", 0),
        "step_count": task.get("progress_count", 0),
        "started_at": task.get("started_at"),
        "finished_at": task.get("completed_at"),
        "interrupt": task.get("interrupt"),
    }


def configure_service_port(service: Any) -> None:
    global _SERVICE
    _SERVICE = service


def configure_executor_port(executor: Any) -> None:
    global _EXECUTOR
    _EXECUTOR = executor


def _service() -> Any:
    if _SERVICE is None:
        raise RuntimeError("subagent session port is not configured")
    return _SERVICE


def _executor() -> Any:
    if _EXECUTOR is None:
        raise RuntimeError("subagent executor port is not configured")
    return _EXECUTOR


class SubagentSessionPort:
    @staticmethod
    async def mark_started(*args: Any, **kwargs: Any) -> Any:
        return await _service().mark_started(*args, **kwargs)

    @staticmethod
    async def persist_projection(*args: Any, **kwargs: Any) -> Any:
        return await _service().persist_projection(*args, **kwargs)

    @staticmethod
    async def mark_waiting_approval(*args: Any, **kwargs: Any) -> Any:
        return await _service().mark_waiting_approval(*args, **kwargs)

    @staticmethod
    async def mark_resumed(*args: Any, **kwargs: Any) -> Any:
        return await _service().mark_resumed(*args, **kwargs)

    @staticmethod
    async def mark_terminal(*args: Any, **kwargs: Any) -> Any:
        return await _service().mark_terminal(*args, **kwargs)

    @staticmethod
    async def collect_partial_output(*args: Any, **kwargs: Any) -> Any:
        return await _service().collect_partial_output(*args, **kwargs)

    # child_session_summary 为模块级纯函数（上方），不经服务委托


class ExecutorPort:
    @staticmethod
    def validate_followup(*args: Any, **kwargs: Any) -> Any:
        return _executor().validate_followup(*args, **kwargs)

    @staticmethod
    def send_message(*args: Any, **kwargs: Any) -> Any:
        return _executor().send_message(*args, **kwargs)

    @staticmethod
    async def asend_message(*args: Any, **kwargs: Any) -> Any:
        return await _executor().asend_message(*args, **kwargs)

    @staticmethod
    def submit_decisions(*args: Any, **kwargs: Any) -> Any:
        return _executor().submit_decisions(*args, **kwargs)

    @staticmethod
    def cancel(*args: Any, **kwargs: Any) -> Any:
        return _executor().cancel(*args, **kwargs)

    @staticmethod
    def subscribe_run_events(*args: Any, **kwargs: Any) -> Any:
        return _executor().subscribe_run_events(*args, **kwargs)

    @staticmethod
    def unsubscribe_run_events(*args: Any, **kwargs: Any) -> Any:
        return _executor().unsubscribe_run_events(*args, **kwargs)

    @staticmethod
    def get_run_event_history(*args: Any, **kwargs: Any) -> Any:
        return _executor().get_run_event_history(*args, **kwargs)


__all__ = [
    "ExecutorPort",
    "SubagentSessionPort",
    "configure_executor_port",
    "configure_service_port",
]
