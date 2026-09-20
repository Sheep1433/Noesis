"""子 Agent 运行器与产品服务之间的窄端口。

接口归消费方（agents.background），实现由 services 侧注册——依赖方向
单一：services → agents（分层允许方向），agents 不 import 任何 service
模块。注册在各服务模块 import 时完成（chat_service /
subagent_session_service / bg_continuation_service 模块尾）。
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
    async def launch(*args: Any, **kwargs: Any) -> Any:
        return await _service().launch(*args, **kwargs)

    @staticmethod
    async def mark_launch_rejected(*args: Any, **kwargs: Any) -> Any:
        return await _service().mark_launch_rejected(*args, **kwargs)

    @staticmethod
    async def create_followup_run(*args: Any, **kwargs: Any) -> Any:
        return await _service().create_followup_run(*args, **kwargs)

    @staticmethod
    async def mark_started(*args: Any, **kwargs: Any) -> Any:
        return await _service().mark_started(*args, **kwargs)

    @staticmethod
    async def persist_projection(*args: Any, **kwargs: Any) -> Any:
        return await _service().persist_projection(*args, **kwargs)

    @staticmethod
    async def mark_terminal(*args: Any, **kwargs: Any) -> Any:
        return await _service().mark_terminal(*args, **kwargs)

    @staticmethod
    async def collect_partial_output(*args: Any, **kwargs: Any) -> Any:
        return await _service().collect_partial_output(*args, **kwargs)

    # child_session_summary 为模块级纯函数（上方），不经服务委托


_SESSION_OPS: Any = None
_CONTINUATION: Any = None


def configure_session_ops_port(service: Any) -> None:
    """注册会话操作实现（ChatService：子会话删除 / extra 合并）。"""
    global _SESSION_OPS
    _SESSION_OPS = service


def configure_continuation_port(func: Any) -> None:
    """注册终态续跑调度实现（bg_continuation_service.schedule_maybe_continue）。"""
    global _CONTINUATION
    _CONTINUATION = func


def _session_ops() -> Any:
    if _SESSION_OPS is None:
        raise RuntimeError("session ops port is not configured")
    return _SESSION_OPS


def _continuation() -> Any:
    if _CONTINUATION is None:
        raise RuntimeError("continuation port is not configured")
    return _CONTINUATION


class SessionOpsPort:
    """会话级操作（ChatService 实现）：kernel 上下文快照合并、launch 失败回滚。"""

    @staticmethod
    async def merge_session_extra(*args: Any, **kwargs: Any) -> Any:
        return await _session_ops().merge_session_extra(*args, **kwargs)

    @staticmethod
    async def delete_session(*args: Any, **kwargs: Any) -> Any:
        return await _session_ops().delete_session(*args, **kwargs)


class ContinuationPort:
    """任务终态后唤醒主 Agent（bg_continuation_service 实现）。"""

    @staticmethod
    async def schedule_maybe_continue(*args: Any, **kwargs: Any) -> Any:
        return await _continuation()(*args, **kwargs)


class ExecutorPort:
    # 单一异步入口（校验折叠在锁内前置）：曾因同步/异步双版本导致端口
    # 白名单漂移（漏 asend_message → 全部 followup 500），收敛为单方法
    @staticmethod
    async def deliver_followup(*args: Any, **kwargs: Any) -> Any:
        return await _executor().deliver_followup(*args, **kwargs)

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


_NOTIFICATION_STORE: Any = None


def configure_notification_store(store: Any) -> None:
    """注册通知存储实现（bg_notification_store：落库 / 送达删行）。"""
    global _NOTIFICATION_STORE
    _NOTIFICATION_STORE = store


class NotificationStorePort:
    """终态通知持久化（services 侧实现；未注册 = 纯内存模式）。

    与其他端口不同，未注册不抛错：record 在大量单测中触发，无服务装配
    时必须静默降级为纯内存（Phase 2 之前的既有行为）。
    """

    @staticmethod
    async def persist(session_id: str, notice: dict) -> None:
        store = _NOTIFICATION_STORE
        if store is not None:
            await store.persist(session_id, notice)

    @staticmethod
    async def delete_delivered(session_id: str, notice_ids: list[str]) -> None:
        store = _NOTIFICATION_STORE
        if store is not None:
            await store.delete_delivered(session_id, notice_ids)


__all__ = [
    "ContinuationPort",
    "ExecutorPort",
    "NotificationStorePort",
    "SessionOpsPort",
    "SubagentSessionPort",
    "configure_continuation_port",
    "configure_executor_port",
    "configure_notification_store",
    "configure_service_port",
    "configure_session_ops_port",
]
