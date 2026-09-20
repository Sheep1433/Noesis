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
    async def create_turn_run(*args: Any, **kwargs: Any) -> Any:
        return await _service().create_turn_run(*args, **kwargs)

    # -- 追加消息受理（pending 行 + 命令同事务；任意实例可用） -----------

    @staticmethod
    async def accept_message(*args: Any, **kwargs: Any) -> dict:
        """受理追加消息：事务写 pending 行 + bg_task_deliver 命令，等待/降级。"""
        return await _service().accept_message(*args, **kwargs)

    @staticmethod
    async def count_pending_messages(session_id: str) -> int:
        """该 child session 的 pending 行计数（队列容量判定）。"""
        return await _service().count_pending_messages(session_id)

    @staticmethod
    async def flip_pending_message_dropped(message_id: str) -> int:
        """单条 pending 行翻转 dropped（投递失败路径；幂等）。"""
        return await _service().flip_pending_message_dropped(message_id)

    @staticmethod
    async def flip_pending_messages_dropped(session_id: str) -> int:
        """该 child session 全部 pending 行翻转 dropped（不可续终态/对账）。"""
        return await _service().flip_pending_messages_dropped(session_id)

    # -- DB 投影与冷恢复 ------------------------------------------------

    @staticmethod
    async def db_task_projection(task_ref: str) -> Optional[dict[str, Any]]:
        """任务 DB 投影（热集 miss 的查询兜底）；无 DB 事实返回 None。

        task_ref = bg_task_id（child session extra）或 child session id。
        """
        return await _service().db_task_projection(task_ref)

    @staticmethod
    async def list_db_task_projections(session_id: str) -> list[dict[str, Any]]:
        return await _service().list_db_task_projections(session_id)

    @staticmethod
    async def load_cold_task(task_ref: str) -> Optional[dict[str, Any]]:
        """冷恢复全量事实：投影 + pending 行（执行镜像重载源）。"""
        return await _service().load_cold_task(task_ref)

    @staticmethod
    async def list_queued_subagent_runs(db: Any = None) -> list[dict[str, Any]]:
        """对账用：queued 状态的 child run 行（created_at 升序）。"""
        return await _service().list_queued_subagent_runs(db=db)

    @staticmethod
    async def mark_terminal_persist_exhausted(run_id: str) -> None:
        """终态落库重试耗尽标记（run 行 error_code 诊断位，不改状态）。"""
        return await _service().mark_terminal_persist_exhausted(run_id)

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
    # 白名单漂移（漏方法 → 全部追加消息请求 500），收敛为单方法
    @staticmethod
    async def deliver_message(*args: Any, **kwargs: Any) -> Any:
        return await _executor().deliver_message(*args, **kwargs)

    @staticmethod
    async def check_with_fallback(*args: Any, **kwargs: Any) -> Any:
        return await _executor().check_with_fallback(*args, **kwargs)

    @staticmethod
    async def list_with_fallback(*args: Any, **kwargs: Any) -> Any:
        return await _executor().list_with_fallback(*args, **kwargs)

    @staticmethod
    async def cancel_with_fallback(*args: Any, **kwargs: Any) -> Any:
        return await _executor().cancel_with_fallback(*args, **kwargs)

    @staticmethod
    async def restore_queued(*args: Any, **kwargs: Any) -> Any:
        return await _executor().restore_queued(*args, **kwargs)

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


_SHELL_JOBS: Any = None


def configure_shell_job_port(service: Any) -> None:
    """注册 shell job 事实行服务（bg_shell_job_service）。"""
    global _SHELL_JOBS
    _SHELL_JOBS = service


def _shell_jobs() -> Any:
    if _SHELL_JOBS is None:
        raise RuntimeError("shell job port is not configured")
    return _SHELL_JOBS


class ShellJobPort:
    """shell 任务事实行（bg_shell_job 表）：落库 / 投影 / 重启对账。"""

    @staticmethod
    async def persist_start(*args: Any, **kwargs: Any) -> None:
        return await _shell_jobs().persist_start(*args, **kwargs)

    @staticmethod
    async def mark_started(task_id: str) -> None:
        return await _shell_jobs().mark_started(task_id)

    @staticmethod
    async def mark_terminal(*args: Any, **kwargs: Any) -> None:
        return await _shell_jobs().mark_terminal(*args, **kwargs)

    @staticmethod
    async def get_task(task_id: str) -> Optional[dict[str, Any]]:
        return await _shell_jobs().get_task(task_id)

    @staticmethod
    async def list_for_session(session_id: str) -> list[dict[str, Any]]:
        return await _shell_jobs().list_for_session(session_id)

    @staticmethod
    async def reconcile_orphaned(db: Any = None) -> int:
        return await _shell_jobs().reconcile_orphaned(db=db)


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
    "ShellJobPort",
    "NotificationStorePort",
    "SessionOpsPort",
    "SubagentSessionPort",
    "configure_continuation_port",
    "configure_executor_port",
    "configure_notification_store",
    "configure_shell_job_port",
    "configure_service_port",
    "configure_session_ops_port",
]
