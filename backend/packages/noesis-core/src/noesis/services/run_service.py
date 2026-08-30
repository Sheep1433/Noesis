"""Agent run 的创建、查询、订阅与停止应用服务。"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
import uuid
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.runtime.logging import logger
from noesis.config.env import DistributedRunsConfig, StreamConfig
from noesis.chat.runs.bus import (
    InMemoryRunBus,
    RunBus,
    WAKEUP_TOPIC_RUN_CREATED,
)
from noesis.chat.runs.launch_payload import LaunchPayload
from noesis.config.code_enum import IntentEnum
from noesis.chat.delivery.events import (
    RunAborted,
    RunError,
    RunEvent,
    WireFrame,
)
from noesis.chat.delivery.sse import parse_sse_line_to_event
from noesis.services.persist_sink import PersistSink
from noesis.chat.message_builder import UserMessageBuilder
from noesis.chat.runs import (
    ACTIVE_RUN_STATUSES,
    RunLimitExceeded,
    RunManager,
    RunSnapshot,
    RunStatus,
    TerminalCandidate,
    TerminalCommitResult,
)
from noesis.chat.message_builder import normalize_message_content_for_delivery
from noesis.chat.runs.projection import RunProjection
from noesis.errors.exceptions import ConflictException, NotFoundException, ServiceException
from noesis.storage.postgres.manager import pg_manager
from noesis.repositories.agent_run_repository import AgentRunRepository
from noesis.storage.postgres.models.chat import TAgentRun, TChatMessage, TChatSession
from noesis.schemas.chat_vo import CreateRunRequest
from noesis.schemas.login_vo import CurrentUser
from noesis.schemas.qa_vo import HitlResumeRequest, TestCaseResumeRequest
from noesis.services.qa import QaService
from noesis.services.chat_service import ChatService
from noesis.services.compaction_service import compact_session


run_manager = RunManager(
    max_buffer_events=StreamConfig.run_event_buffer_max_events,
    max_buffer_bytes=StreamConfig.run_event_buffer_max_bytes,
    subscriber_queue_events=StreamConfig.run_subscriber_queue_max_events,
    subscriber_queue_bytes=StreamConfig.run_subscriber_queue_max_bytes,
    max_active_runs=StreamConfig.run_max_active,
    max_user_active_runs=StreamConfig.run_max_active_per_user,
    max_subscriptions_per_run=StreamConfig.run_max_subscriptions_per_run,
    max_subscriptions_per_user=StreamConfig.run_max_subscriptions_per_user,
    max_subscriptions_global=StreamConfig.run_max_subscriptions_global,
    terminal_retention_seconds=StreamConfig.run_terminal_retention_seconds,
    max_run_duration_seconds=StreamConfig.run_max_duration_seconds,
    max_output_bytes=StreamConfig.run_max_output_bytes,
    hitl_pending_timeout_seconds=StreamConfig.run_hitl_pending_timeout_seconds,
    cancel_grace_seconds=StreamConfig.run_cancel_grace_seconds,
    terminal_persistence_budget_seconds=StreamConfig.run_terminal_persistence_budget_seconds,
    terminal_retry_interval_seconds=StreamConfig.run_terminal_retry_interval_seconds,
    checkpoint_retry_interval_seconds=StreamConfig.persistence_retry_interval_seconds,
)


def _create_run_bus(settings: DistributedRunsConfig) -> RunBus:
    if settings.backend == "redis":
        # P4 接入 RedisRunBus；在此之前 redis 模式不提供「退化成 memory」的静默路径
        raise NotImplementedError(
            "redis run bus adapter 尚未接入（enable-distributed-sse-pubsub P4）"
        )
    return InMemoryRunBus(
        envelope_payload_max_bytes=settings.envelope_payload_max_bytes
    )


run_bus = _create_run_bus(DistributedRunsConfig)


# 注入 run_manager 给命令层（/status），避免 noesis.chat 直接 import noesis.services。
from noesis.chat.commands.runtime import set_run_manager_provider  # noqa: E402
from noesis.chat.commands.runtime import set_compaction_provider  # noqa: E402

set_run_manager_provider(lambda: run_manager)
set_compaction_provider(
    lambda session_id, user_id, instructions=None: compact_session(
        session_id=session_id,
        user_id=user_id,
        instructions=instructions,
    )
)


# 注入「建新会话」工厂给命令层（/new），避免 noesis.chat 直接 import noesis.services。
# 工厂用 pg_manager 自建 db session（非 FastAPI 请求上下文，如 telegram poll loop）。
from noesis.chat.commands.runtime import set_session_factory_provider  # noqa: E402


async def _create_session_for_command(
    user_id: str, title: str | None = None, parent_id: str | None = None
) -> str:
    async with pg_manager.get_async_session_context() as db:
        session = await ChatService.create_session(
            user_id=user_id, title=title, parent_id=parent_id, db=db
        )
        return str(session.id)


set_session_factory_provider(lambda: _create_session_for_command)


def _now_ms() -> int:
    return int(time.time() * 1000)


class CheckpointGuarded(RuntimeError):
    """checkpoint 被 sequence guard 拒绝（DB last_sequence >= incoming）。
    不是错误——迟到 checkpoint 不应覆盖更新的 DB snapshot。"""


def _request_digest(request: CreateRunRequest) -> str:
    payload = request.model_dump(mode="json", exclude={"client_request_id"})
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class RunService:
    @staticmethod
    async def publish_projected_event(run_id, projection, event, publish):
        """原子 apply + publish：projection.apply 在 RunHandle lock 内执行，
        保证 snapshot sequence 与 projection 内容一致。

        publish 是 RunManager.apply_event 的引用（由 _run_producer 的 lambda 传入），
        它在 lock 内完成 apply、sequence 分配、buffer 写入和 subscriber fan-out。
        """
        # advance_attempt 逻辑已移入 apply_event（lock 内同步 handle.attempt_id）
        # StreamDone + HITL_PENDING 检查已移入 RunProjection.apply（返回 False）
        return await publish(event, projection.attempt_id)

    @classmethod
    async def create(
        cls,
        request: CreateRunRequest,
        current_user: CurrentUser,
        db: AsyncSession,
    ) -> TAgentRun:
        user_id = str(current_user.user_id)
        digest = _request_digest(request)
        repository = AgentRunRepository(db)
        existing = await repository.get_by_client_request(user_id, request.client_request_id)
        if existing is not None:
            if existing.request_digest != digest:
                raise ConflictException(
                    message="请求标识已用于其他消息", data={"run_id": existing.id}
                )
            # 幂等重放：仍 queued 未 claim 则补发唤醒（wake-up 丢失由 dispatcher 补扫兜底）
            if existing.status == RunStatus.QUEUED.value and not existing.owner_instance_id:
                await cls._wakeup_run_created(existing.id)
            return existing

        active = await repository.get_active_for_session(user_id, request.session_id)
        if active is not None:
            raise ConflictException(
                message="当前会话仍在生成",
                data={
                    "run_id": active.id,
                    "assistant_message_id": active.assistant_message_id,
                    "session_id": active.session_id,
                    "status": active.status,
                },
            )

        now = _now_ms()
        run_id = str(uuid.uuid4())
        assistant_message_id = str(uuid.uuid4())
        extra = dict(request.extra or {})
        qa_type = str(extra.get("qa_type") or "")
        try:
            session, message_sequences = await ChatService.reserve_message_sequences(
                request.session_id, user_id, 2, db
            )
        except ServiceException as exc:
            raise NotFoundException(message="会话不存在") from exc
        # 请求未显式指定 qa_type 时回退读会话 extra（定时任务会话续聊复用 SuperAgent 等类型）。
        if not qa_type:
            session_extra = getattr(session, "extra", None) or {}
            qa_type = str(session_extra.get("qa_type") or IntentEnum.COMMON_QA.value[0])
            extra["qa_type"] = qa_type
        ChatService.apply_default_session_title(session, request.content)
        user_message = TChatMessage(
            id=str(uuid.uuid4()),
            session_id=request.session_id,
            parent_id=None,
            user_id=user_id,
            role="user",
            content=UserMessageBuilder(request.content.strip()).to_dict(),
            extra={"qa_type": qa_type, **extra},
            status="completed",
            message_sequence=message_sequences[0],
            created_at=now,
        )
        assistant_message = TChatMessage(
            id=assistant_message_id,
            session_id=request.session_id,
            parent_id=user_message.id,
            user_id=user_id,
            role="assistant",
            content={"parts": []},
            extra={"qa_type": qa_type, "run_id": run_id},
            status="streaming",
            message_sequence=message_sequences[1],
            created_at=now,
        )
        # create 时冻结 model identity：排队期间会话默认模型变化不影响本 run
        if qa_type == IntentEnum.TEST_CASE_QA.value[0]:
            resolved_model: str | None = None
        else:
            from noesis.services.qa.helpers import _resolve_model_for_query

            resolved_model = await _resolve_model_for_query(
                session_id=request.session_id,
                user_id=user_id,
                request_model_id=str(extra["model_id"]) if extra.get("model_id") else None,
                db=db,
            )
        launch_payload = LaunchPayload.from_create_request(
            request,
            user_id=user_id,
            assistant_message_id=assistant_message_id,
            qa_type=qa_type,
            origin="web",
            resolved_model=resolved_model,
        )
        run = TAgentRun(
            id=run_id,
            user_id=user_id,
            session_id=request.session_id,
            assistant_message_id=assistant_message_id,
            client_request_id=request.client_request_id,
            request_digest=digest,
            qa_type=qa_type,
            origin="web",
            status=RunStatus.QUEUED.value,
            last_sequence=0,
            attempt_id=1,
            owner_instance_id=None,
            owner_term=0,
            launch_payload=launch_payload.to_dict(),
            snapshot={"parts": []},
            created_at=now,
            updated_at=now,
        )
        try:
            # ORM 未声明 relationship，须显式建立 FK 插入顺序。两次 flush 与 session
            # 更新时间都必须位于同一异常边界内；execute 会触发 autoflush，不能只捕获 commit。
            db.add(user_message)
            db.add(assistant_message)
            await db.flush()
            db.add(run)
            await db.flush()
            await db.execute(
                update(TChatSession)
                .where(TChatSession.id == request.session_id, TChatSession.user_id == user_id)
                .values(updated_at=now)
            )
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            existing = await repository.get_by_client_request(user_id, request.client_request_id)
            if existing is None:
                active = await repository.get_active_for_session(user_id, request.session_id)
                if active is not None:
                    # 确有活跃 run（含 queued 僵尸）——业务冲突，提示并返回完整字段以便前端加入
                    raise ConflictException(
                        message="当前会话仍在生成",
                        data={
                            "run_id": active.id,
                            "assistant_message_id": active.assistant_message_id,
                            "session_id": active.session_id,
                            "status": active.status,
                        },
                    )
                # 非活跃 run 冲突的约束违规（如 FK/校验）——不能误报成"当前会话仍在生成"，
                # 否则会掩盖真实插入失败。抛出带原始约束信息的错误。
                logger.exception(
                    "create run IntegrityError (non-active-conflict) session_id={} client_request_id={}",
                    request.session_id,
                    request.client_request_id,
                )
                raise ServiceException(message="创建任务失败，请稍后重试") from exc
            if existing.request_digest != digest:
                raise ConflictException(message="请求标识已用于其他消息")
            if existing.status == RunStatus.QUEUED.value and not existing.owner_instance_id:
                await cls._wakeup_run_created(existing.id)
            return existing

        # 提交后唤醒 dispatcher；失败只记日志（补扫兜底），不影响创建结果
        await cls._wakeup_run_created(run.id)
        return run

    @classmethod
    async def _wakeup_run_created(cls, run_id: str) -> None:
        try:
            await run_bus.wakeup(WAKEUP_TOPIC_RUN_CREATED, {"run_id": run_id})
        except Exception:
            # 唤醒失败不回滚创建：dispatcher 周期补扫兜底
            logger.exception("run-created wakeup failed run_id={}", run_id)

    @classmethod
    async def _finalize_start_failure(cls, run: TAgentRun) -> None:
        content: dict[str, Any] = {"parts": []}
        try:
            handle = run_manager.get(run.id)
        except KeyError:
            handle = None
        if handle is not None:
            if isinstance(handle.state, RunProjection):
                handle.state.cancel_requested = True
                content = handle.state.builder.to_dict()
            await run_manager.stop(run.id)

        now = _now_ms()
        async with pg_manager.get_async_session_context() as cleanup_db:
            won = await AgentRunRepository(cleanup_db).finalize(
                run_id=run.id,
                target=RunStatus.ERROR,
                assistant_status="error",
                content=content,
                last_sequence=(
                    handle.last_sequence
                    if handle is not None
                    else int(getattr(run, "last_sequence", 0))
                ),
                finished_at=now,
                finish_reason="start_failed",
                error_code="RUN_START_FAILED",
                user_error_message="任务启动失败，请稍后重试",
            )
            if won:
                await cleanup_db.commit()
            else:
                await cleanup_db.rollback()

    @classmethod
    async def start_queued_run(
        cls,
        run: TAgentRun,
        payload: LaunchPayload,
        current_user: CurrentUser,
    ) -> None:
        """启动已 claim 的 queued Run（dispatcher 调用；幂等，重复启动直接返回）。

        run 参数须为 DB 权威行（launch_payload / owner_term 已就位）；
        current_user 由调用方从 DB 用户记录重建，不依赖请求进程上下文。
        """
        try:
            run_manager.get(run.id)
            return
        except KeyError:
            pass
        if RunStatus(run.status) != RunStatus.QUEUED:
            return

        projection = RunProjection(
            run_id=run.id,
            user_id=str(run.user_id),
            session_id=run.session_id,
            assistant_message_id=run.assistant_message_id,
            qa_type=run.qa_type,
            origin=run.origin,
            status=RunStatus.RUNNING,
            attempt_id=run.attempt_id,
        )
        qa_request = payload.to_qa_query_request()
        persist_sink = PersistSink(
            checkpoint_interval_seconds=StreamConfig.checkpoint_interval_seconds
        )

        async def persist_checkpoint(request) -> None:
            await cls._persist_checkpoint(
                request.run_id,
                request.assistant_message_id,
                request.snapshot,
                request.snapshot_sequence,
            )

        def checkpoint_policy(event: RunEvent, _sequence: int) -> str | None:
            persist_sink.on_event(event)
            return persist_sink.checkpoint_kind(event)

        async def producer(publish) -> None:
            async with pg_manager.get_async_session_context() as run_db:
                try:
                    stream = QaService.exec_query(
                        qa_request,
                        current_user,
                        run_db,
                        assistant_message_id=run.assistant_message_id,
                        run_id=run.id,
                    )
                    if run.qa_type == IntentEnum.TEST_CASE_QA.value[0]:
                        # TEST_CASE_QA 不纳入本次 typed 主路径迁移；维持隔离的
                        # CaseCoordinator SSE 边界，避免兼容 parser 污染目标 qa_type。
                        async for line in stream:
                            if not isinstance(line, str):
                                raise TypeError("TEST_CASE_QA must emit SSE strings")
                            for event in parse_sse_line_to_event(line):
                                await publish(event, projection.attempt_id)
                    else:
                        async for event in stream:
                            if isinstance(event, str):
                                raise TypeError(
                                    f"target Agent Run emitted SSE string qa_type={run.qa_type}"
                                )
                            event_attempt_id = (
                                event.attempt_id
                                if isinstance(event, WireFrame)
                                and event.attempt_id is not None
                                else projection.attempt_id
                            )
                            await publish(event, event_attempt_id)
                    await run_manager.drain_persistence(run.id)
                except BaseException as exc:
                    if isinstance(exc, GeneratorExit):
                        raise
                    await cls._persist_cancel_or_error(run.id, projection, exc)
                    if not isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        return
                    raise

        async def persist_limit(error: RunLimitExceeded) -> None:
            await cls._persist_cancel_or_error(run.id, projection, error)

        await run_manager.start(
            run_id=run.id,
            session_id=run.session_id,
            user_id=str(run.user_id),
            assistant_message_id=run.assistant_message_id,
            snapshot_provider=projection.snapshot,
            producer=producer,
            attempt_id=run.attempt_id,
            state=projection,
            limit_handler=persist_limit,
            checkpoint_policy=checkpoint_policy,
            checkpoint_handler=persist_checkpoint,
            terminal_handler=cls._persist_terminal_candidate,
            max_run_duration_seconds=(
                StreamConfig.run_max_duration_seconds_super_agent
                if run.qa_type == IntentEnum.SUPER_AGENT_QA.value[0]
                else None
            ),
        )
        async with pg_manager.get_async_session_context() as state_db:
            await AgentRunRepository(state_db).compare_and_set_status(
                run.id,
                [RunStatus.QUEUED],
                RunStatus.RUNNING,
                started_at=_now_ms(),
                updated_at=_now_ms(),
            )
            await state_db.commit()

    @classmethod
    async def _persist_checkpoint(
        cls,
        run_id: str,
        assistant_message_id: str,
        snapshot: RunSnapshot,
        sequence: int,
    ) -> None:
        """写入 immutable checkpoint snapshot。snapshot 在 apply_event lock 内捕获，
        与 sequence 绑定。DB UPDATE 带 sequence guard：迟到 checkpoint 不覆盖更新状态。"""
        deadline = time.monotonic() + StreamConfig.persistence_timeout_seconds
        last_error: Exception | None = None
        content = {"parts": [dict(part) for part in snapshot.parts]}
        persisted = snapshot.to_dict()
        if snapshot.pending_hitl is not None:
            persisted["_pending_hitl"] = dict(snapshot.pending_hitl)
        while time.monotonic() < deadline:
            try:
                async with pg_manager.get_async_session_context() as db:
                    stored = await AgentRunRepository(db).save_checkpoint(
                        run_id=run_id,
                        assistant_message_id=assistant_message_id,
                        sequence=sequence,
                        snapshot=persisted,
                        content=content,
                        attempt_id=snapshot.attempt_id,
                        status=snapshot.status,
                        finish_reason=snapshot.finish_reason,
                        updated_at=_now_ms(),
                    )
                    if stored:
                        await db.commit()
                    else:
                        # sequence guard 拒绝：迟到 checkpoint 不覆盖更新的 DB snapshot。
                        # 记录 metric 并抛异常，使 PersistWriter 的 on_persisted 不被调用
                        # （避免 last_persisted_sequence 被错误更新到被拒绝的 sequence）。
                        await db.rollback()
                        run_manager.record_checkpoint_failure(run_id)
                        logger.info(
                            "agent_run_checkpoint_guarded run_id={} sequence={} "
                            "(DB last_sequence >= incoming, ignored)",
                            run_id,
                            sequence,
                        )
                        raise CheckpointGuarded(
                            f"checkpoint sequence {sequence} guarded by newer DB state"
                        )
                return
            except CheckpointGuarded:
                # 被守卫拒绝不是错误，不需要重试
                return
            except Exception as exc:
                last_error = exc
                run_manager.record_checkpoint_failure(run_id)
                await asyncio.sleep(StreamConfig.persistence_retry_interval_seconds)
        raise RuntimeError("agent run checkpoint persistence timeout") from last_error

    @classmethod
    async def _persist_terminal_candidate(
        cls, candidate: TerminalCandidate
    ) -> TerminalCommitResult:
        projection = candidate.projected_state
        if not isinstance(projection, RunProjection):
            raise TypeError("terminal candidate requires RunProjection")
        content = projection.builder.to_dict()
        assistant_status = {
            RunStatus.COMPLETED: "completed",
            RunStatus.PARTIAL: "partial",
            RunStatus.ERROR: "error",
            RunStatus.INTERRUPTED: "partial",
        }[candidate.status]
        async with pg_manager.get_async_session_context() as db:
            repository = AgentRunRepository(db)
            won = await repository.finalize(
                run_id=candidate.envelope.run_id,
                target=candidate.status,
                assistant_status=assistant_status,
                content=content,
                last_sequence=candidate.envelope.sequence,
                snapshot=projection.persisted_snapshot(),
                finished_at=_now_ms(),
                finish_reason=projection.finish_reason or "stop",
                error_code=projection.error_code,
                user_error_message=projection.user_error_message,
                usage=projection.run_usage,
            )
            if won:
                await db.commit()
                return TerminalCommitResult("committed")
            await db.rollback()
            row = await repository.get(candidate.envelope.run_id)
            if row is None or RunStatus(row.status) not in {
                RunStatus.COMPLETED,
                RunStatus.PARTIAL,
                RunStatus.ERROR,
                RunStatus.INTERRUPTED,
            }:
                return TerminalCommitResult("failed")
            return TerminalCommitResult(
                "already_finalized", cls._snapshot_from_row(row)
            )

    @classmethod
    async def _persist_projection(cls, run_id: str, projection: RunProjection) -> None:
        target = projection.status
        if target == RunStatus.RUNNING:
            target = RunStatus.COMPLETED
        if target == RunStatus.ERROR:
            logger.error(
                "agent run projected terminal error run_id={} session_id={} error_code={} message={}",
                run_id,
                projection.session_id,
                projection.error_code or "RUN_FAILED",
                projection.user_error_message or "",
            )
        async with pg_manager.get_async_session_context() as db:
            repository = AgentRunRepository(db)
            content = projection.builder.to_dict()
            now = _now_ms()
            if target in {
                RunStatus.COMPLETED,
                RunStatus.PARTIAL,
                RunStatus.ERROR,
                RunStatus.INTERRUPTED,
            }:
                assistant_status = {
                    RunStatus.COMPLETED: "completed",
                    RunStatus.PARTIAL: "partial",
                    RunStatus.ERROR: "error",
                    RunStatus.INTERRUPTED: "partial",
                }[target]
                won = await repository.finalize(
                    run_id=run_id,
                    target=target,
                    assistant_status=assistant_status,
                    content=content,
                    last_sequence=run_manager.get(run_id).last_sequence,
                    snapshot=projection.persisted_snapshot(),
                    finished_at=now,
                    finish_reason=projection.finish_reason or (
                        "server_restart" if target == RunStatus.INTERRUPTED else "stop"
                    ),
                    error_code=projection.error_code,
                    user_error_message=projection.user_error_message,
                    usage=projection.run_usage,
                )
                if not won:
                    await db.rollback()
                    return
            else:
                await repository.compare_and_set_status(
                    run_id,
                    [RunStatus.RUNNING, RunStatus.RETRYING, RunStatus.HITL_PENDING],
                    target,
                    last_sequence=run_manager.get(run_id).last_sequence,
                    snapshot=projection.persisted_snapshot(),
                    finish_reason=projection.finish_reason,
                    error_code=projection.error_code,
                    user_error_message=projection.user_error_message,
                    updated_at=now,
                )
            await db.commit()
        await run_manager.transition(run_id, target)

    @classmethod
    async def _persist_cancel_or_error(
        cls, run_id: str, projection: RunProjection, exc: BaseException
    ) -> None:
        if isinstance(exc, RunLimitExceeded):
            logger.warning(
                "agent run stopped by limit run_id={} session_id={} status={} exception_type={}",
                run_id,
                projection.session_id,
                projection.status.value,
                type(exc).__name__,
            )
        else:
            logger.opt(exception=exc).error(
                "agent run producer failed run_id={} session_id={} status={} exception_type={}",
                run_id,
                projection.session_id,
                projection.status.value,
                type(exc).__name__,
            )
        handle = run_manager.get(run_id)
        limit_error = (
            exc
            if isinstance(exc, RunLimitExceeded)
            else getattr(handle, "limit_error", None)
        )
        stopped = isinstance(exc, asyncio.CancelledError) and limit_error is None
        has_content = bool(projection.builder.to_dict().get("parts"))
        target = RunStatus.PARTIAL if stopped or (limit_error is not None and has_content) else RunStatus.ERROR
        reason = "stopped" if stopped else "limit_exceeded" if limit_error is not None else "error"
        error_code = limit_error.error_code if limit_error is not None else None if stopped else "RUN_FAILED"
        user_message = (
            "本轮生成时间过长，已停止"
            if error_code == "RUN_TIMEOUT"
            else "等待确认已超时"
            if error_code == "HITL_TIMEOUT"
            else "本轮生成内容过长，已停止"
            if error_code == "LIMIT_EXCEEDED"
            else None
            if stopped
            else "生成失败，请稍后重试"
        )
        terminal_event: RunEvent
        if target == RunStatus.PARTIAL:
            terminal_event = RunAborted(
                reason=reason,
                error_code=error_code,
                message=user_message,
            )
        else:
            terminal_event = RunError(
                message=user_message or "生成失败，请稍后重试",
                finish_reason=reason,
                error_code=error_code,
            )
        await run_manager.apply_event(run_id, terminal_event, attempt_id=handle.attempt_id)

    @staticmethod
    def _snapshot_from_row(row: TAgentRun) -> RunSnapshot:
        raw_snapshot = row.snapshot if isinstance(row.snapshot, dict) else {}
        parts = normalize_message_content_for_delivery(raw_snapshot).get("parts", [])
        pending_hitl = (
            row.snapshot.get("_pending_hitl")
            if isinstance(row.snapshot, dict)
            and isinstance(row.snapshot.get("_pending_hitl"), dict)
            else None
        )
        return RunSnapshot(
            run_id=row.id,
            user_id=str(row.user_id),
            session_id=row.session_id,
            assistant_message_id=row.assistant_message_id,
            qa_type=row.qa_type,
            origin=row.origin,
            status=RunStatus(row.status),
            sequence=row.last_sequence,
            attempt_id=row.attempt_id,
            parts=tuple(parts),
            finish_reason=row.finish_reason,
            error_code=row.error_code,
            user_error_message=row.user_error_message,
            pending_hitl=pending_hitl,
            updated_at=row.updated_at,
        )

    @classmethod
    async def get(cls, run_id: str, user_id: str, db: AsyncSession) -> RunSnapshot:
        repository = AgentRunRepository(db)
        row = await repository.get(run_id, user_id)
        if row is None:
            raise NotFoundException(message="任务不存在")
        try:
            handle = run_manager.get(run_id)
        except KeyError:
            return cls._snapshot_from_row(row)
        if handle.authoritative_snapshot is not None:
            return copy.deepcopy(handle.authoritative_snapshot)
        return handle.snapshot_provider(handle.last_sequence, handle.status, handle.attempt_id)

    @classmethod
    async def get_active_run(
        cls, session_id: str, user_id: str, db: AsyncSession
    ) -> RunSnapshot | None:
        """查询 session 下的 active Run，返回完整 RunSnapshot 或 None。

        优先返回 live owner 的实时 snapshot；owner 不可达时返回 DB snapshot。
        对外契约：与 GET /runs/{run_id} 结构一致。
        """
        session = await ChatService.get_session_by_id(session_id, user_id, db)
        if session is None:
            raise NotFoundException(message="会话不存在")
        repository = AgentRunRepository(db)
        row = await repository.get_active_for_session(user_id, session_id)
        if row is None:
            return None
        try:
            handle = run_manager.get(row.id)
        except KeyError:
            return cls._snapshot_from_row(row)
        if handle.authoritative_snapshot is not None:
            return copy.deepcopy(handle.authoritative_snapshot)
        return handle.snapshot_provider(handle.last_sequence, handle.status, handle.attempt_id)

    @classmethod
    async def get_latest_for_session(
        cls, session_id: str, user_id: str, db: AsyncSession
    ) -> RunSnapshot | None:
        """查询会话最近一轮 run，供会话级 SSE 入口选择默认 run。"""
        session = await ChatService.get_session_by_id(session_id, user_id, db)
        if session is None:
            raise NotFoundException(message="会话不存在")
        row = await AgentRunRepository(db).get_latest_for_session(user_id, session_id)
        if row is None:
            return None
        return await cls.get(row.id, user_id, db)

    @classmethod
    async def stop(cls, run_id: str, user_id: str, db: AsyncSession) -> RunSnapshot:
        row = await AgentRunRepository(db).get(run_id, user_id)
        if row is None:
            raise NotFoundException(message="任务不存在")
        try:
            await run_manager.stop(run_id)
        except KeyError:
            pass
        # 兜底：内存 stop 后 DB 行仍非终态时强制收口。两种来源——
        # 进程重启后 run 不在注册表（stop 全程 no-op），或 producer 取消
        # 收尾失败（二次 cancel 打断 DB 写入）。不收口则 UI 永远显示
        # 生成中，且行状态与内存注册表永久不一致。
        await db.refresh(row)
        if row.status in ACTIVE_RUN_STATUSES:
            await cls._force_finalize_stopped(row, db)
        return await cls.get(run_id, user_id, db)

    @classmethod
    async def _force_finalize_stopped(cls, row: TAgentRun, db: AsyncSession) -> None:
        """用户停止的强制终态：沿用 run 当前快照内容（保留已生成的进度）。"""
        from noesis.chat.event_mapping.failure_notice import mark_running_tools_unknown

        content = mark_running_tools_unknown(
            row.snapshot if isinstance(row.snapshot, dict) else {"parts": []}
        )
        finalized = await AgentRunRepository(db).finalize(
            run_id=row.id,
            target=RunStatus.INTERRUPTED,
            assistant_status="partial",
            content=content,
            last_sequence=row.last_sequence,
            finished_at=_now_ms(),
            finish_reason="stopped",
            error_code="USER_STOP_FORCE",
            user_error_message="本轮回复已被用户中断。",
            snapshot=content,
        )
        if finalized:
            await db.commit()
            logger.warning(
                "run 停止兜底收口（内存 stop 未达终态）run_id={} session_id={}",
                row.id, row.session_id,
            )

    @classmethod
    async def subscribe(cls, run_id: str, user_id: str, after_sequence: int, db: AsyncSession):
        row = await AgentRunRepository(db).get(run_id, user_id)
        if row is None:
            raise NotFoundException(message="任务不存在")
        try:
            return await run_manager.subscribe(run_id, after_sequence=after_sequence)
        except KeyError:
            return None

    @classmethod
    async def resume_hitl(
        cls,
        run_id: str,
        request: HitlResumeRequest,
        current_user: CurrentUser,
        db: AsyncSession,
    ) -> RunSnapshot:
        row = await AgentRunRepository(db).get(run_id, str(current_user.user_id))
        if row is None:
            raise NotFoundException(message="任务不存在")
        if row.status != RunStatus.HITL_PENDING.value:
            raise ConflictException(message="任务当前不需要确认", data={"run_id": run_id})
        try:
            handle = run_manager.get(run_id)
        except KeyError as exc:
            raise ConflictException(message="本轮任务已中断，无法继续确认") from exc
        if not isinstance(handle.state, RunProjection):
            raise ConflictException(message="任务状态无法恢复")
        projection = handle.state
        pending_payload = projection.pending_hitl
        if (
            not isinstance(pending_payload, dict)
            or str(pending_payload.get("interrupt_id") or "") != request.interrupt_id
        ):
            raise ConflictException(message="确认请求已失效，请刷新后重试")
        from noesis.chat.hitl.pending import PendingHitl

        pending = PendingHitl(
            interrupt_id=request.interrupt_id,
            session_id=row.session_id,
            user_id=str(current_user.user_id),
            assistant_message_id=row.assistant_message_id,
            expires_at=float(pending_payload.get("expires_at") or 0),
            kind=str(pending_payload.get("kind") or "approval"),
            action_requests=list(pending_payload.get("action_requests") or []),
            review_configs=list(pending_payload.get("review_configs") or []),
        )
        if pending.expires_at > 0 and pending.expires_at <= time.time():
            raise ConflictException(message="确认请求已超时，请重新发起")
        async def producer(publish) -> None:
            async with pg_manager.get_async_session_context() as run_db:
                try:
                    async for event in QaService.exec_hitl_resume(
                        pending=pending,
                        decisions=[item.model_dump(exclude_none=True) for item in request.decisions],
                        grant_scope=request.grant_scope,
                        current_user=current_user,
                        db=run_db,
                        run_id=run_id,
                    ):
                        await publish(event, projection.attempt_id)
                    await run_manager.drain_persistence(run_id)
                except BaseException as exc:
                    await cls._persist_cancel_or_error(run_id, projection, exc)

        decisions = [item.model_dump(exclude_none=True) for item in request.decisions]
        repository = AgentRunRepository(db)
        won = await repository.compare_and_set_status(
            run_id,
            [RunStatus.HITL_PENDING],
            RunStatus.RUNNING,
            updated_at=_now_ms(),
        )
        if not won:
            await db.rollback()
            raise ConflictException(message="确认请求已被处理，请刷新后重试")
        await db.commit()
        logger.info(
            "agent run HITL resume accepted run_id={} session_id={} interrupt_id={}",
            run_id,
            row.session_id,
            request.interrupt_id,
        )
        try:
            await run_manager.resume(
                run_id,
                producer,
                prepare=lambda: (
                    projection.apply_hitl_decisions(decisions),
                    projection.begin_hitl_resume(),
                ),
            )
        except Exception:
            logger.exception("agent run HITL resume start failed run_id={}", run_id)
            await repository.compare_and_set_status(
                run_id,
                [RunStatus.RUNNING],
                RunStatus.HITL_PENDING,
                updated_at=_now_ms(),
            )
            await db.commit()
            raise ServiceException(message="继续任务失败，请稍后重试")
        return handle.snapshot_provider(handle.last_sequence, handle.status, handle.attempt_id)

    @classmethod
    async def resume_test_case(
        cls,
        run_id: str,
        request: TestCaseResumeRequest,
        current_user: CurrentUser,
        db: AsyncSession,
    ) -> RunSnapshot:
        row = await AgentRunRepository(db).get(run_id, str(current_user.user_id))
        if row is None:
            raise NotFoundException(message="任务不存在")
        if row.qa_type != IntentEnum.TEST_CASE_QA.value[0]:
            raise ConflictException(message="当前任务不是测试用例生成")
        if row.status != RunStatus.HITL_PENDING.value:
            raise ConflictException(message="任务当前不需要确认", data={"run_id": run_id})
        try:
            handle = run_manager.get(run_id)
        except KeyError as exc:
            raise ConflictException(message="本轮任务已中断，无法继续确认") from exc
        if not isinstance(handle.state, RunProjection):
            raise ConflictException(message="任务状态无法恢复")
        projection = handle.state
        async def producer(publish) -> None:
            async with pg_manager.get_async_session_context() as run_db:
                try:
                    async for line in QaService.exec_test_case_resume(
                        session_id=row.session_id,
                        selected_point_names=request.selected_point_names,
                        current_user=current_user,
                        db=run_db,
                        assistant_message_id=row.assistant_message_id,
                    ):
                        for event in parse_sse_line_to_event(line):
                            envelope = await cls.publish_projected_event(
                                run_id, projection, event, publish
                            )
                            if envelope is None:
                                continue
                    await run_manager.drain_delivery(run_id, "persist")
                    await cls._persist_projection(run_id, projection)
                except BaseException as exc:
                    await cls._persist_cancel_or_error(run_id, projection, exc)

        repository = AgentRunRepository(db)
        won = await repository.compare_and_set_status(
            run_id,
            [RunStatus.HITL_PENDING],
            RunStatus.RUNNING,
            updated_at=_now_ms(),
        )
        if not won:
            await db.rollback()
            raise ConflictException(message="确认请求已被处理，请刷新后重试")
        await db.commit()
        try:
            await run_manager.resume(
                run_id,
                producer,
                prepare=lambda: setattr(projection, "status", RunStatus.RUNNING),
            )
        except Exception:
            logger.exception("test case run resume start failed run_id={}", run_id)
            await repository.compare_and_set_status(
                run_id,
                [RunStatus.RUNNING],
                RunStatus.HITL_PENDING,
                updated_at=_now_ms(),
            )
            await db.commit()
            raise ServiceException(message="继续任务失败，请稍后重试")
        return handle.snapshot_provider(handle.last_sequence, handle.status, handle.attempt_id)
