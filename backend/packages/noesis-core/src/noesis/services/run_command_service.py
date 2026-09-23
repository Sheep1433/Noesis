"""跨进程 durable command：提交入口与 leader consumer（task 5.1–5.3）。

任意 worker 提交命令落库（幂等去重）并经 Run bus 唤醒 leader；leader
consumer 认领执行（stop / HITL resume / 后台任务停止），wake-up 丢失由
有界补扫兜底。两种运行模式共用同一条状态机——memory 模式 leader 即本
进程，命令同样经落库与 bus 唤醒路径。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from noesis.ids import now_ms
from noesis.runtime.logging import logger
from noesis.chat.runs.bus import WAKEUP_TOPIC_RUN_COMMAND
from noesis.errors.exceptions import ConflictException, NotFoundException
from noesis.repositories.agent_run_command_repository import (
    AgentRunCommandRepository,
    CommandDigestConflict,
)
from noesis.repositories.agent_run_repository import AgentRunRepository
from noesis.storage.postgres.manager import pg_manager

# 提交后对完成的有界等待（task 5.4）：leader 同进程的常见路径（stop 在
# cancel grace 内完成）返回 completed；超时返回 accepted，不伪装完成
_COMMAND_ACK_WAIT_SECONDS = 5.0


class RunCommandService:
    """命令提交入口（任意 worker）与提交-等待观察。"""

    @classmethod
    async def submit_stop(cls, run_id: str, user_id: str, db) -> dict[str, Any]:
        """主 Run 停止：按 (run_id, stop) 幂等。"""
        row = await AgentRunRepository(db).get(run_id, user_id)
        if row is None:
            raise NotFoundException(message="任务不存在")
        return await cls._submit(
            db, user_id=user_id, command_type="stop",
            dedupe_key=f"run:{run_id}:stop", run_id=run_id,
        )

    @classmethod
    async def submit_subagent_stop(cls, run_id: str, user_id: str, db) -> dict[str, Any]:
        """子 Agent run 停止（bg_task_stop 族：leader 上 executor.cancel）。"""
        row = await AgentRunRepository(db).get(run_id, user_id)
        if row is None or row.origin != "subagent":
            raise NotFoundException(message="子 Agent 任务不存在")
        task_id = row.session_id  # 子会话 run 的 session 即任务公开身份
        return await cls._submit(
            db, user_id=user_id, command_type="bg_task_stop",
            dedupe_key=f"bg:{task_id}:stop", run_id=run_id, task_id=task_id,
        )

    @classmethod
    async def submit_shell_stop(
        cls, task_id: str, session_id: str, user_id: str, db
    ) -> dict[str, Any]:
        """后台命令停止：校验会话归属后按任务幂等提交。"""
        from noesis.storage.postgres.models.chat import TChatSession
        from sqlalchemy import select

        result = await db.execute(
            select(TChatSession.id).where(
                TChatSession.id == session_id, TChatSession.user_id == user_id
            )
        )
        if result.scalar_one_or_none() is None:
            raise NotFoundException(message="会话不存在")
        return await cls._submit(
            db, user_id=user_id, command_type="bg_task_stop",
            dedupe_key=f"bg:{task_id}:stop", task_id=task_id,
        )

    @classmethod
    async def submit_deliver(
        cls, *, child_session_id: str, message_id: str, user_id: str, db,
    ) -> dict[str, Any]:
        """追加消息消费命令（与 pending 行同一事务：flush 不自提交）。"""
        return await cls._submit(
            db, user_id=user_id, command_type="bg_task_deliver",
            dedupe_key=f"bg:{child_session_id}:deliver:{message_id}",
            task_id=child_session_id, flush=True, wakeup=False,
            payload={"child_session_id": child_session_id, "message_id": message_id},
        )

    @staticmethod
    async def wakeup_command(command_id: str) -> None:
        await RunCommandService._wakeup({"command_id": command_id})

    @classmethod
    async def wait_for_command(
        cls, command_id: str, *, timeout_seconds: float = _COMMAND_ACK_WAIT_SECONDS,
    ) -> dict[str, Any]:
        """对已提交命令的有界等待：completed/rejected/no_op 即返，超时 accepted。"""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            async with pg_manager.get_async_session_context() as db:
                row = await AgentRunCommandRepository(db).get(command_id)
            if row is not None and row.status != "pending":
                return {
                    "command_id": row.id,
                    "command_status": row.status,
                    "result_summary": row.result_summary,
                }
        return {"command_id": command_id, "command_status": "accepted"}

    @classmethod
    async def submit_hitl_resume(
        cls, run_id: str, user_id: str, interrupt_id: str, decision: dict, db
    ) -> dict[str, Any]:
        """HITL 恢复：按 (run_id, interrupt_id) 幂等 + decision digest 冲突检测。"""
        from noesis.repositories.agent_run_command_repository import decision_digest

        row = await AgentRunRepository(db).get(run_id, user_id)
        if row is None:
            raise NotFoundException(message="任务不存在")
        return await cls._submit(
            db, user_id=user_id, command_type="hitl_resume",
            dedupe_key=f"run:{run_id}:hitl:{interrupt_id}", run_id=run_id,
            decision_digest_value=decision_digest(decision),
            payload={"interrupt_id": interrupt_id, "decision": decision},
        )

    @classmethod
    async def _submit(cls, db, *, user_id, command_type, dedupe_key, wakeup: bool = True, **kwargs) -> dict[str, Any]:
        repository = AgentRunCommandRepository(db)
        try:
            row = await repository.submit(
                user_id=user_id, command_type=command_type,
                dedupe_key=dedupe_key, **kwargs,
            )
        except CommandDigestConflict:
            raise ConflictException(
                message="该确认已有不同决策在处理，请刷新后重试",
                data={"dedupe_key": dedupe_key},
            )
        if not wakeup:
            return cls._to_dict(row)
        await cls._wakeup({"command_id": row.id})
        return cls._to_dict(row)

    @staticmethod
    async def _wakeup(payload: dict[str, str]) -> None:
        """经 Run bus 唤醒 leader consumer；失败由补扫兜底（不回滚命令）。"""
        try:
            from noesis.services.run_service import run_bus

            await run_bus.wakeup(WAKEUP_TOPIC_RUN_COMMAND, payload)
        except Exception:  # noqa: BLE001
            logger.debug("command wakeup failed（补扫兜底）payload={}", payload)

    @staticmethod
    def _to_dict(row) -> dict[str, Any]:
        return {
            "command_id": row.id,
            "command_type": row.type,
            "command_status": row.status,
            "run_id": row.run_id,
            "task_id": row.task_id,
            "created_at": row.created_at,
        }

    @classmethod
    async def submit_and_wait(cls, submit_coro, *, db) -> dict[str, Any]:
        """提交 + 对完成的有界等待（纯读取观察，超时返回 accepted）。"""
        result = await submit_coro
        command_id = result["command_id"]
        if result["command_status"] != "pending":
            return result
        deadline = time.monotonic() + _COMMAND_ACK_WAIT_SECONDS
        while time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            row = await AgentRunCommandRepository(db).get(command_id)
            if row is not None and row.status != "pending":
                result = cls._to_dict(row)
                break
        return result


    @classmethod
    async def stop_shell_job(cls, *, task_id: str, session_id: str, user_id: str) -> dict[str, Any]:
        """后台命令停止（HTTP 入口的完整用例）：独立会话提交并等待确认。

        命令提交与观察需要一个独立于请求级 session 的事务边界
        （提交即 commit，等待期间轮询不受请求事务影响），该编排属于
        Service，API 不持有存储设施句柄。
        """
        from noesis.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as cmd_db:
            return await cls.submit_and_wait(
                cls.submit_shell_stop(task_id, session_id, user_id, cmd_db),
                db=cmd_db,
            )


class RunCommandConsumer:
    """leader 侧命令 consumer：bus 唤醒 + 周期补扫，认领后重验执行。"""

    def __init__(
        self,
        *,
        bus: Any,
        scan_interval_seconds: float = 5.0,
        retention_days: float = 7.0,
        cleanup_interval_seconds: float = 3600.0,
        claim_lease_seconds: float = 30.0,
        shard_filter: Callable[[Any], bool] | None = None,
    ) -> None:
        self._bus = bus
        # worker-role-split 命令分片：worker 注入「命令目标在本进程持有」
        # 判定（run_manager 注册表 / executor 热集）；None = 不过滤
        #（单进程部署 / control 消费全局命令）
        self._shard_filter = shard_filter
        self._scan_interval = scan_interval_seconds
        self._claim_lease_ms = int(max(5.0, claim_lease_seconds) * 1000)
        self._retention_days = retention_days
        self._cleanup_interval = cleanup_interval_seconds
        self._task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None
        self._wakeup_subscription: Any = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._wakeup_subscription = self._bus.subscribe_wakeups()
        await self._wakeup_subscription.ready()
        self._task = asyncio.create_task(
            self._loop(), name="run-command-consumer"
        )
        # 保留期清理（task 5.6）：低频批量删除超期终态命令；保留期=去重窗口
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(), name="run-command-cleanup"
        )

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(self._cleanup_interval)
            try:
                await self._cleanup_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("run command cleanup error")

    async def _cleanup_once(self) -> int:
        async with pg_manager.get_async_session_context() as db:
            deleted = await AgentRunCommandRepository(db).cleanup_expired(
                retention_days=self._retention_days
            )
        if deleted:
            logger.info("run command cleanup deleted={} retention_days={}", deleted, self._retention_days)
        return deleted

    async def stop(self) -> None:
        cleanup = self._cleanup_task
        self._cleanup_task = None
        if cleanup is not None and not cleanup.done():
            cleanup.cancel()
            try:
                await cleanup
            except (asyncio.CancelledError, Exception):
                pass
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if self._wakeup_subscription is not None:
            await self._wakeup_subscription.close()
            self._wakeup_subscription = None

    async def _loop(self) -> None:
        while True:
            try:
                await self._consume_once()
                wakeup = self._wakeup_subscription
                if wakeup is not None:
                    try:
                        await asyncio.wait_for(
                            wakeup.__aiter__().__anext__(),
                            timeout=self._scan_interval,
                        )
                    except asyncio.TimeoutError:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("run command consumer tick error")
                await asyncio.sleep(self._scan_interval)

    async def _consume_once(self) -> int:
        async with pg_manager.get_async_session_context() as db:
            await AgentRunCommandRepository(db).reset_stale_claimed(
                lease_ms=self._claim_lease_ms,
            )
            rows = await AgentRunCommandRepository(db).claim_pending(
                shard_filter=self._shard_filter,
            )
        for row in rows:
            await self._execute(row)
        return len(rows)

    async def _execute(self, row) -> None:
        try:
            summary = await self._dispatch(row)
            if summary is not None and summary.startswith(("deferred:", "cancelled:")):
                # 延后/意图保留：消费语义未发生，命令记 no_op（非 completed）
                status = "no_op"
            else:
                status = "completed" if summary is not None else "no_op"
        except NotFoundException:
            status, summary = "no_op", "目标不存在或已完成"
        except ConflictException as exc:
            status, summary = "rejected", getattr(exc, "message", None) or str(exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "run command execute failed command_id={} type={}", row.id, row.type
            )
            status, summary = "rejected", f"执行失败：{type(exc).__name__}"
        try:
            async with pg_manager.get_async_session_context() as db:
                await AgentRunCommandRepository(db).mark_terminal(row.id, status, summary)
        except Exception:  # noqa: BLE001
            logger.exception("run command mark terminal failed command_id={}", row.id)

    async def _dispatch(self, row) -> str | None:
        """执行已认领命令；返回摘要（None = no_op）。执行前重验状态。"""
        from noesis.services.run_service import RunService

        user_id = str(row.user_id)
        async with pg_manager.get_async_session_context() as db:
            if row.type == "stop" and row.run_id:
                snapshot = await RunService.stop(row.run_id, user_id, db)
                return f"stopped:{snapshot.status.value}"
            if row.type == "hitl_resume" and row.run_id:
                payload = dict(row.payload or {})
                decision = dict(payload.get("decision") or {})
                run_row = await AgentRunRepository(db).get(row.run_id, user_id)
                if run_row is not None and run_row.origin == "subagent":
                    from noesis.services.subagent_session_service import (
                        SubagentSessionService,
                    )

                    await SubagentSessionService.resume_hitl(
                        run_id=row.run_id, user_id=user_id,
                        decisions=decision.get("decisions") or decision, db=db,
                    )
                    return "resumed"
                request = _build_hitl_request(payload)
                await RunService.resume_hitl(
                    row.run_id, request, _current_user_stub(user_id), db
                )
                return "resumed"
            if row.type == "bg_task_stop":
                return await self._stop_bg_task(row, user_id)
            if row.type == "bg_task_deliver":
                return await self._deliver_bg_task(row, user_id)
        raise ValueError(f"unknown command type: {row.type}")

    async def _deliver_bg_task(self, row, user_id: str) -> str | None:
        """追加消息消费：四分派——幂等空转 / 延后 / 冷恢复或入队 / 拒绝翻转。

        行状态与任务状态的权威裁决都在本方法（消费端）；受理端的校验只是
        咨询性快速失败。任务非终态但热集 miss（换主窗口）→ no_op 延后，
        不得翻转行（排队任务的指示随任务重建保留）。
        """
        from noesis.agents.background.executor import BackgroundTaskExecutor
        from noesis.agents.background.jobs.state import BgTaskStatus
        from noesis.services.subagent_session_service import SubagentSessionService

        payload = dict(row.payload or {})
        child_session_id = str(payload.get("child_session_id") or row.task_id or "")
        message_id = str(payload.get("message_id") or "")
        if not child_session_id or not message_id:
            return None
        info = await SubagentSessionService.message_delivery_payload(message_id)
        if info is None or not info["pending"]:
            return None  # 已采纳（launch 事务清标记）或已翻转：幂等空转
        current = BackgroundTaskExecutor.get(child_session_id)
        if current is not None:
            status = BgTaskStatus(str(current["status"]))
        else:
            projection = await SubagentSessionService.db_task_projection(child_session_id)
            status = (
                BgTaskStatus(str(projection["status"]))
                if projection else None
            )
        if status is None:
            return None  # 任务事实缺失：无从消费，命令记 no_op
        if status in (BgTaskStatus.FAILED, BgTaskStatus.TIMED_OUT):
            # 对账 error 已在受理前把行翻转 dropped；此处兜底消费拒绝
            await SubagentSessionService.flip_pending_message_dropped(message_id)
            raise ConflictException(message=f"任务已结束（{status.value}），追加消息未执行")
        if status in (BgTaskStatus.QUEUED, BgTaskStatus.RUNNING) and current is None:
            # 换主窗口：对账/排队重建尚未落定 → 延后（行保留 pending；
            # 重建后随任务镜像重载消费）
            return f"deferred:{status.value}"
        if status == BgTaskStatus.CANCELLED:
            completed_at = (current or {}).get("completed_at") or (
                (await SubagentSessionService.db_task_projection(child_session_id)) or {}
            ).get("completed_at")
            cancelled_ms = int(completed_at * 1000) if completed_at else 0
            if cancelled_ms and info["created_at"] < cancelled_ms:
                # 受理先于用户停止：意图保留（行保留 pending，待续聊触发），不复活任务
                return "cancelled:queued_intent_retained"
        executor = BackgroundTaskExecutor.default()
        await executor.deliver_message(
            child_session_id,
            info["text"],
            user_message_id=message_id,
            model_id=info["model_id"],
            reasoning_effort=info["reasoning_effort"],
        )
        return "delivered"

    async def _stop_bg_task(self, row, user_id: str) -> str | None:
        """后台任务停止：leader 上 executor.cancel；不存在/已终态幂等 no_op。"""
        from noesis.agents.background.executor import BackgroundTaskExecutor

        target = row.task_id or row.run_id
        if not target:
            return None
        current = BackgroundTaskExecutor.get(str(target))
        if current is None:
            # 注册表无此任务：已终态或随进程丢失——幂等收口
            return None
        try:
            result = BackgroundTaskExecutor.cancel(str(target))
        except ValueError:
            # get 与 cancel 间任务终态清理：幂等 no_op
            return None
        return f"cancelled:{result.get('status')}"

def _build_hitl_request(payload: dict):
    from noesis.schemas.chat_vo import HitlResumeRequest

    decision = dict(payload.get("decision") or {})
    interrupt_id = str(payload.get("interrupt_id") or decision.get("interrupt_id") or "")
    return HitlResumeRequest(interrupt_id=interrupt_id, **_hitl_fields(decision))


def _hitl_fields(decision: dict) -> dict:
    return {k: v for k, v in decision.items() if k != "interrupt_id"}


def _current_user_stub(user_id: str):
    from noesis.schemas.login_vo import CurrentUser

    return CurrentUser(user_id=user_id, username="")
