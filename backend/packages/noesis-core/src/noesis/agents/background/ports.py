"""子 Agent 运行器与产品服务之间的窄端口。

端口与适配器（ports & adapters）：每个端口 = 一条跨层调用方向的契约面，
接口归消费方（agents.background），实现由 services 侧注册——依赖方向
单一：services → agents（分层允许方向），agents 不 import 任何 service
模块（运行时依赖；类型仅经 ``TYPE_CHECKING`` 引入）。

转发方法带真实签名——调用方拼错参数在端口边界即暴露，而非深入服务
内部才炸；``tests/test_port_contracts.py`` 用 inspect 钉住"实现签名 ==
转发签名"（参数名/种类/默认值），签名漂移在 CI 即红。

注册在各服务模块 import 时完成；组合根经
``services.runtime_ports.register_runtime_ports`` 保证启动前就绪。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession

    from noesis.chat.runs import RunStatus
    from noesis.services.subagent_session_service import ChildSessionLaunch

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


# --------------------------------------------------------------------------
# SubagentSessionPort：子 Agent 全生命周期（SubagentSessionService 实现）
# --------------------------------------------------------------------------


def configure_service_port(service: Any) -> None:
    global _SERVICE
    _SERVICE = service


def _service() -> Any:
    if _SERVICE is None:
        raise RuntimeError("subagent session port is not configured")
    return _SERVICE


class SubagentSessionPort:
    @staticmethod
    async def launch(
        *,
        parent_session_id: str,
        user_id: str,
        description: str,
        prompt: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        model_id: Optional[str] = None,
        subagent_type: str = "general",
        db: AsyncSession,
    ) -> "ChildSessionLaunch":
        return await _service().launch(
            parent_session_id=parent_session_id,
            user_id=user_id,
            description=description,
            prompt=prompt,
            tool_call_id=tool_call_id,
            model_id=model_id,
            subagent_type=subagent_type,
            db=db,
        )

    @staticmethod
    async def mark_started(run_id: str, started_at: Optional[int] = None) -> None:
        return await _service().mark_started(run_id, started_at)

    @staticmethod
    async def mark_launch_rejected(run_id: str, error: str) -> None:
        return await _service().mark_launch_rejected(run_id, error)

    @staticmethod
    async def create_turn_run(
        *,
        session_id: str,
        user_id: str,
        message: str,
        user_message_id: Optional[str] = None,
        db: AsyncSession,
    ) -> "ChildSessionLaunch":
        return await _service().create_turn_run(
            session_id=session_id,
            user_id=user_id,
            message=message,
            user_message_id=user_message_id,
            db=db,
        )

    # -- 追加消息受理（pending 行 + 命令同事务；任意实例可用） -----------

    @staticmethod
    async def accept_message(
        *,
        task_ref: str,
        user_id: str,
        message: str,
        model_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> dict:
        """受理追加消息：事务写 pending 行 + bg_task_deliver 命令，等待/降级。"""
        return await _service().accept_message(
            task_ref=task_ref,
            user_id=user_id,
            message=message,
            model_id=model_id,
            reasoning_effort=reasoning_effort,
        )

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
    async def db_task_projection(task_ref: str) -> Optional[dict]:
        """任务 DB 投影（热集 miss 的查询兜底）；无 DB 事实返回 None。

        task_ref = bg_task_id（child session extra）或 child session id。
        """
        return await _service().db_task_projection(task_ref)

    @staticmethod
    async def list_db_task_projections(session_id: str) -> list[dict]:
        return await _service().list_db_task_projections(session_id)

    @staticmethod
    async def load_cold_task(task_ref: str) -> Optional[dict]:
        """冷恢复全量事实：投影 + pending 行（执行镜像重载源）。"""
        return await _service().load_cold_task(task_ref)

    @staticmethod
    async def list_queued_subagent_runs(db: AsyncSession) -> list[dict]:
        """对账用：queued 状态的 child run 行（created_at 升序）。"""
        return await _service().list_queued_subagent_runs(db=db)

    @staticmethod
    async def mark_terminal_persist_exhausted(run_id: str) -> None:
        """终态落库重试耗尽标记（run 行 error_code 诊断位，不改状态）。"""
        return await _service().mark_terminal_persist_exhausted(run_id)

    @staticmethod
    async def persist_projection(
        *,
        run_id: str,
        assistant_message_id: str,
        content: dict,
        sequence: int,
    ) -> None:
        return await _service().persist_projection(
            run_id=run_id,
            assistant_message_id=assistant_message_id,
            content=content,
            sequence=sequence,
        )

    @staticmethod
    async def mark_terminal(
        *,
        run_id: str,
        status: "RunStatus",
        content: Optional[dict] = None,
        error: Optional[str] = None,
        finish_reason: Optional[str] = None,
        usage: Optional[dict] = None,
        model_calls: Optional[list] = None,
    ) -> None:
        return await _service().mark_terminal(
            run_id=run_id,
            status=status,
            content=content,
            error=error,
            finish_reason=finish_reason,
            usage=usage,
            model_calls=model_calls,
        )

    @staticmethod
    async def collect_partial_output(session_id: str, user_id: str) -> str:
        return await _service().collect_partial_output(session_id, user_id)

    # child_session_summary 为模块级纯函数（上方），不经服务委托


# --------------------------------------------------------------------------
# SessionOpsPort：会话级操作（ChatService 实现）
# --------------------------------------------------------------------------


_SESSION_OPS: Any = None


def configure_session_ops_port(service: Any) -> None:
    """注册会话操作实现（ChatService：子会话删除 / extra 合并）。"""
    global _SESSION_OPS
    _SESSION_OPS = service


def _session_ops() -> Any:
    if _SESSION_OPS is None:
        raise RuntimeError("session ops port is not configured")
    return _SESSION_OPS


class SessionOpsPort:
    @staticmethod
    async def merge_session_extra(
        session_id: str,
        user_id: str,
        patch: dict,
        db: Optional[AsyncSession] = None,
    ) -> None:
        return await _session_ops().merge_session_extra(session_id, user_id, patch, db=db)

    @staticmethod
    async def delete_session(
        session_id: str,
        user_id: str,
        db: Optional[AsyncSession] = None,
    ) -> bool:
        return await _session_ops().delete_session(session_id, user_id, db=db)


# --------------------------------------------------------------------------
# ContinuationPort：终态唤醒（bg_continuation_service 实现）
# --------------------------------------------------------------------------


_CONTINUATION: Any = None


def configure_continuation_port(func: Any) -> None:
    """注册终态续跑调度实现（bg_continuation_service.schedule_maybe_continue）。"""
    global _CONTINUATION
    _CONTINUATION = func


def _continuation() -> Any:
    if _CONTINUATION is None:
        raise RuntimeError("continuation port is not configured")
    return _CONTINUATION


class ContinuationPort:
    """任务终态后唤醒主 Agent（60s 去抖 + 连续唤醒上限由实现负责）。"""

    @staticmethod
    async def schedule_maybe_continue(session_id: str, user_id: str) -> None:
        # 注册的实现是模块级函数（bg_continuation_service.schedule_maybe_continue），
        # 直接调用——不是带方法的对象
        return await _continuation()(session_id, user_id)


# --------------------------------------------------------------------------
# ExecutorPort：服务层指挥后台执行器（反向端口）
# --------------------------------------------------------------------------


def configure_executor_port(executor: Any) -> None:
    global _EXECUTOR
    _EXECUTOR = executor


def _executor() -> Any:
    if _EXECUTOR is None:
        raise RuntimeError("subagent executor port is not configured")
    return _EXECUTOR


class ExecutorPort:
    # 单一异步追加消息入口（校验折叠在锁内前置）：曾因同步/异步双版本导致
    # 端口白名单漂移（漏方法 → 全部追加消息请求 500），收敛为单方法
    @staticmethod
    async def deliver_message(
        task_id: str,
        message: str,
        user_message_id: Optional[str] = None,
        model_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> dict:
        return await _executor().deliver_message(
            task_id, message, user_message_id, model_id, reasoning_effort
        )

    @staticmethod
    async def check_with_fallback(task_id: str) -> Optional[dict]:
        return await _executor().check_with_fallback(task_id)

    @staticmethod
    async def list_with_fallback(session_id: str) -> list[dict]:
        return await _executor().list_with_fallback(session_id)

    @staticmethod
    async def cancel_with_fallback(task_id: str) -> dict:
        return await _executor().cancel_with_fallback(task_id)

    @staticmethod
    async def restore_queued(specs: list[dict]) -> int:
        return await _executor().restore_queued(specs)

    @staticmethod
    def cancel(task_id: str) -> dict:
        return _executor().cancel(task_id)

    @staticmethod
    def subscribe_run_events(run_id: str, user_id: str) -> "asyncio.Queue":
        return _executor().subscribe_run_events(run_id, user_id)

    @staticmethod
    def unsubscribe_run_events(run_id: str, queue: "asyncio.Queue") -> None:
        return _executor().unsubscribe_run_events(run_id, queue)

    @staticmethod
    def get_run_event_history(run_id: str, after_sequence: int = 0) -> list[dict]:
        return _executor().get_run_event_history(run_id, after_sequence)


# --------------------------------------------------------------------------
# ShellJobPort：shell 任务事实行（bg_shell_job_service 实现）
# --------------------------------------------------------------------------


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
    async def persist_start(
        *,
        task_id: str,
        session_id: str,
        user_id: str,
        command: str,
        status: str,
    ) -> None:
        return await _shell_jobs().persist_start(
            task_id=task_id,
            session_id=session_id,
            user_id=user_id,
            command=command,
            status=status,
        )

    @staticmethod
    async def mark_started(task_id: str) -> None:
        return await _shell_jobs().mark_started(task_id)

    @staticmethod
    async def mark_terminal(
        *,
        task_id: str,
        status: str,
        error: Optional[str],
        result_tail: Optional[str],
        completed_at: Optional[float],
    ) -> None:
        return await _shell_jobs().mark_terminal(
            task_id=task_id,
            status=status,
            error=error,
            result_tail=result_tail,
            completed_at=completed_at,
        )

    @staticmethod
    async def get_task(task_id: str) -> Optional[dict[str, Any]]:
        return await _shell_jobs().get_task(task_id)

    @staticmethod
    async def list_for_session(session_id: str) -> list[dict[str, Any]]:
        return await _shell_jobs().list_for_session(session_id)

    @staticmethod
    async def reconcile_orphaned(db: Any = None) -> int:
        return await _shell_jobs().reconcile_orphaned(db=db)


# --------------------------------------------------------------------------
# NotificationStorePort：终态通知持久化（bg_notification_store 实现）
# --------------------------------------------------------------------------


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
    "ShellJobPort",
    "SubagentSessionPort",
    "child_session_summary",
    "configure_continuation_port",
    "configure_executor_port",
    "configure_notification_store",
    "configure_service_port",
    "configure_session_ops_port",
    "configure_shell_job_port",
]
