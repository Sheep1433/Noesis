"""Agent run 的创建、查询、订阅与停止应用服务。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.runtime.logging import logger
from noesis.config.env import StreamConfig
from noesis_server.constants.code_enum import IntentEnum
from noesis_server.domain.chat.delivery.events import (
    HitlRequired,
    RunAborted,
    RunCompleted,
    RunError,
    RunEvent,
    RunPaused,
    StreamDone,
    WireFrame,
)
from noesis_server.domain.chat.delivery.sse import parse_sse_line_to_event
from noesis_server.domain.chat.delivery.persist_sink import PersistSink
from noesis_server.domain.chat.message_builder import AssistantMessageBuilder, UserMessageBuilder
from noesis_server.domain.chat.tool_state import ToolState
from noesis_server.domain.chat.runs import RunLimitExceeded, RunManager, RunSnapshot, RunStatus
from noesis_server.exceptions.exception import ConflictException, NotFoundException, ServiceException
from noesis_server.infrastructure.database.engine import AsyncSessionLocal
from noesis_server.infrastructure.database.repositories.agent_run import AgentRunRepository
from noesis_server.models.chat_models import TAgentRun, TChatMessage, TChatSession
from noesis_server.schemas.chat_vo import CreateRunRequest
from noesis_server.schemas.login_vo import CurrentUser
from noesis_server.schemas.qa_vo import QaQueryRequest
from noesis_server.schemas.qa_vo import HitlResumeRequest, TestCaseResumeRequest
from noesis_server.services.qa import QaService
from noesis_server.services.chat_service import ChatService


_OWNER_INSTANCE_ID = f"{socket.gethostname()}:{os.getpid()}"
run_manager = RunManager(
    max_buffer_events=StreamConfig.run_event_buffer_max_events,
    max_buffer_bytes=StreamConfig.run_event_buffer_max_bytes,
    subscriber_queue_events=StreamConfig.run_subscriber_queue_max_events,
    subscriber_queue_bytes=StreamConfig.run_subscriber_queue_max_bytes,
    max_active_runs=StreamConfig.run_max_active,
    max_user_active_runs=StreamConfig.run_max_active_per_user,
    terminal_retention_seconds=StreamConfig.run_terminal_retention_seconds,
    max_run_duration_seconds=StreamConfig.run_max_duration_seconds,
    max_output_bytes=StreamConfig.run_max_output_bytes,
    hitl_pending_timeout_seconds=StreamConfig.run_hitl_pending_timeout_seconds,
    cancel_grace_seconds=StreamConfig.run_cancel_grace_seconds,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _request_digest(request: CreateRunRequest) -> str:
    payload = request.model_dump(mode="json", exclude={"client_request_id"})
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass
class RunProjection:
    run_id: str
    user_id: str
    session_id: str
    assistant_message_id: str
    qa_type: str
    origin: str = "web"
    status: RunStatus = RunStatus.RUNNING
    attempt_id: int = 1
    finish_reason: str | None = None
    error_code: str | None = None
    user_error_message: str | None = None
    retry_attempt: int = 0
    retry_max: int = 0
    cancel_requested: bool = False
    visible_output_started: bool = False
    side_effect_boundary_crossed: bool = False
    pending_hitl: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.builder = AssistantMessageBuilder(
            session_id=self.session_id, message_id=self.assistant_message_id
        )

    def apply(self, event: RunEvent, *, attempt_id: int | None = None) -> bool:
        if attempt_id is not None and attempt_id != self.attempt_id:
            logger.warning(
                "忽略旧 attempt 事件 run_id={} event_attempt={} current_attempt={}",
                self.run_id,
                attempt_id,
                self.attempt_id,
            )
            return False
        if self.status in {
            RunStatus.COMPLETED,
            RunStatus.PARTIAL,
            RunStatus.ERROR,
            RunStatus.INTERRUPTED,
        } and isinstance(event, (RunCompleted, RunAborted, RunError)):
            logger.warning(
                "忽略终态 run 的迟到终态事件 run_id={} status={} event={}",
                self.run_id,
                self.status.value,
                type(event).__name__,
            )
            return False
        if isinstance(event, WireFrame):
            data = event.data
            if event.event == "text-delta":
                delta = data.get("text_delta") or data.get("delta") or data.get("content")
                self.visible_output_started = self.visible_output_started or bool(
                    delta
                )
                self.builder.append_text_delta(
                    str(delta or ""),
                    parent_task_call_id=data.get("parent_task_call_id"),
                )
            elif event.event == "reasoning-delta":
                delta = data.get("text_delta") or data.get("delta") or data.get("content")
                self.visible_output_started = self.visible_output_started or bool(
                    delta
                )
                self.builder.append_reasoning_delta(
                    str(delta or ""),
                    parent_task_call_id=data.get("parent_task_call_id"),
                )
            elif event.event in {"tool-call-start", "tool-input-start"}:
                self.side_effect_boundary_crossed = True
                if event.event == "tool-input-start":
                    return True
                self.builder.append_tool(
                    str(data.get("tool_name") or data.get("name") or "tool"),
                    data.get("input") if isinstance(data.get("input"), dict) else {},
                    str(data.get("tool_call_id") or ""),
                    data.get("parent_task_call_id"),
                    state=data.get("state") or ToolState.RUNNING,
                )
            elif event.event == "tool-input-available":
                self.side_effect_boundary_crossed = True
                self.builder.append_tool(
                    str(data.get("tool_name") or data.get("name") or "tool"),
                    data.get("input") if isinstance(data.get("input"), dict) else {},
                    str(data.get("tool_call_id") or ""),
                    data.get("parent_task_call_id"),
                    state=data.get("state") or ToolState.RUNNING,
                )
            elif event.event == "tool-output-available":
                if self.cancel_requested or self.status in {
                    RunStatus.COMPLETED,
                    RunStatus.PARTIAL,
                    RunStatus.ERROR,
                    RunStatus.INTERRUPTED,
                }:
                    logger.warning(
                        "忽略 run 终止后的工具结果 run_id={} tool_call_id={}",
                        self.run_id,
                        data.get("tool_call_id"),
                    )
                    return
                try:
                    self.builder.append_tool_output(
                        str(data.get("tool_name") or data.get("name") or "tool"),
                        str(data.get("output") or ""),
                        str(data.get("tool_call_id") or ""),
                        status=str(data.get("status") or "success"),
                        state=data.get("state"),
                        error=str(data.get("error")) if data.get("error") else None,
                        error_category=(
                            str(data.get("errorCategory")) if data.get("errorCategory") else None
                        ),
                        outcome=str(data.get("outcome")) if data.get("outcome") else None,
                        exit_code=(int(data["exit_code"]) if data.get("exit_code") is not None else None),
                        timed_out=(bool(data["timed_out"]) if data.get("timed_out") is not None else None),
                        truncated=(bool(data["truncated"]) if data.get("truncated") is not None else None),
                        duration_ms=(int(data["duration_ms"]) if data.get("duration_ms") is not None else None),
                    )
                except ValueError:
                    logger.warning(
                        "忽略无匹配 tool start 的 run 投影结果 run_id={} tool_call_id={}",
                        self.run_id,
                        data.get("tool_call_id"),
                    )
            elif event.event == "run-status":
                status = str(data.get("status") or "")
                event_attempt_id = int(data.get("attempt_id") or self.attempt_id)
                if event_attempt_id >= self.attempt_id:
                    self.attempt_id = event_attempt_id
                if status in {item.value for item in RunStatus}:
                    self.status = RunStatus(status)
                self.retry_attempt = int(data.get("attempt") or self.retry_attempt)
                self.retry_max = int(data.get("max_attempts") or self.retry_max)
        elif isinstance(event, HitlRequired):
            self.side_effect_boundary_crossed = True
            self.status = RunStatus.HITL_PENDING
            self.pending_hitl = dict(event.payload)
            for action in event.payload.get("action_requests") or []:
                if not isinstance(action, dict):
                    continue
                tool_call_id = str(action.get("tool_call_id") or "")
                self.builder.update_tool_hitl(
                    tool_call_id,
                    {
                        "kind": event.payload.get("kind") or "approval",
                        "status": "pending",
                        "interrupt_id": event.payload.get("interrupt_id"),
                    },
                    status="running",
                    state=ToolState.APPROVAL_PENDING,
                )
            keep = {
                str(action.get("tool_call_id") or "")
                for action in event.payload.get("action_requests") or []
                if isinstance(action, dict) and action.get("tool_call_id")
            }
            self.builder.reconcile_nonterminal_tools(
                ToolState.CANCELLED,
                "本次工具执行已停止",
                keep_approval_call_ids=keep,
            )
        elif isinstance(event, RunPaused):
            self.status = RunStatus.HITL_PENDING
            self.finish_reason = event.finish_reason or event.reason
        elif isinstance(event, RunCompleted):
            self.builder.reconcile_nonterminal_tools(ToolState.CANCELLED, "本次工具执行已停止")
            self.status = RunStatus.COMPLETED
            self.finish_reason = event.finish_reason
            self.pending_hitl = None
        elif isinstance(event, RunAborted):
            self.builder.reconcile_nonterminal_tools(ToolState.CANCELLED, "本次工具执行已停止")
            self.status = RunStatus.PARTIAL
            self.finish_reason = event.reason
        elif isinstance(event, RunError):
            terminal_state = (
                ToolState.TIMED_OUT
                if event.finish_reason in {"timeout", "hitl_timeout", "run_timeout"}
                else ToolState.FAILED
            )
            self.builder.reconcile_nonterminal_tools(terminal_state, event.message)
            self.status = RunStatus.ERROR
            self.finish_reason = event.finish_reason
            self.user_error_message = event.message
        return True

    def can_retry_model(self) -> bool:
        return not self.visible_output_started and not self.side_effect_boundary_crossed

    def begin_retry_attempt(self) -> int:
        if not self.can_retry_model():
            raise ValueError("model retry crossed output or side-effect boundary")
        self.attempt_id += 1
        self.retry_attempt = self.attempt_id
        self.status = RunStatus.RETRYING
        return self.attempt_id

    def begin_hitl_resume(self) -> None:
        if self.status != RunStatus.HITL_PENDING:
            raise ValueError("run is not waiting for HITL")
        self.status = RunStatus.RUNNING
        self.pending_hitl = None

    def apply_hitl_decisions(self, decisions: list[dict[str, Any]]) -> None:
        payload = self.pending_hitl or {}
        actions = payload.get("action_requests") or []
        for index, decision in enumerate(decisions):
            action = actions[index] if index < len(actions) and isinstance(actions[index], dict) else {}
            tool_call_id = action.get("tool_call_id")
            decision_type = str(decision.get("type") or "")
            if decision_type == "approve":
                self.builder.update_tool_hitl(
                    tool_call_id,
                    {"status": "approved", "decision": "approve"},
                    status="running",
                    state=ToolState.RUNNING,
                )
            elif decision_type == "reject":
                self.builder.update_tool_hitl(
                    tool_call_id,
                    {"status": "rejected", "decision": "reject"},
                    status="error",
                    state=ToolState.REJECTED,
                )
            elif decision_type == "respond":
                self.builder.update_tool_hitl(
                    tool_call_id,
                    {"status": "answered", "decision": "respond"},
                    status="success",
                    state=ToolState.SUCCEEDED,
                )

    def snapshot(self, sequence: int, status: RunStatus, attempt_id: int) -> RunSnapshot:
        effective_status = self.status if self.status != RunStatus.RUNNING else status
        return RunSnapshot(
            run_id=self.run_id,
            user_id=self.user_id,
            session_id=self.session_id,
            assistant_message_id=self.assistant_message_id,
            qa_type=self.qa_type,
            origin=self.origin,
            status=effective_status,
            sequence=sequence,
            attempt_id=attempt_id,
            parts=tuple(self.builder.to_dict().get("parts", [])),
            finish_reason=self.finish_reason,
            error_code=self.error_code,
            user_error_message=self.user_error_message,
            retry_attempt=self.retry_attempt,
            retry_max=self.retry_max,
            pending_hitl=self.pending_hitl,
            updated_at=_now_ms(),
        )

    def persisted_snapshot(self) -> dict[str, Any]:
        snapshot = self.builder.to_dict()
        if self.pending_hitl is not None:
            snapshot["_pending_hitl"] = dict(self.pending_hitl)
        return snapshot


class RunService:
    @staticmethod
    async def publish_projected_event(run_id, projection, event, publish):
        if isinstance(event, WireFrame) and event.event == "run-status":
            event_attempt_id = int(event.data.get("attempt_id") or projection.attempt_id)
            if event_attempt_id > projection.attempt_id:
                await run_manager.advance_attempt(run_id, event_attempt_id)
        projection.apply(event)
        # LangGraph 每个执行分段都会发 [DONE]。HITL pause 后仍属于同一个 Run，
        # 不能把分段结束投递成整个订阅结束。
        if isinstance(event, StreamDone) and projection.status == RunStatus.HITL_PENDING:
            return None
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
            await cls._ensure_started_or_finalize(existing, request, current_user)
            return existing

        active = await repository.get_active_for_session(user_id, request.session_id)
        if active is not None:
            raise ConflictException(message="当前会话仍在生成", data={"run_id": active.id})

        now = _now_ms()
        run_id = str(uuid.uuid4())
        assistant_message_id = str(uuid.uuid4())
        extra = dict(request.extra or {})
        qa_type = str(extra.get("qa_type") or IntentEnum.COMMON_QA.value[0])
        try:
            session, message_sequences = await ChatService.reserve_message_sequences(
                request.session_id, user_id, 2, db
            )
        except ServiceException as exc:
            raise NotFoundException(message="会话不存在") from exc
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
            retry_attempt=0,
            retry_max=0,
            owner_instance_id=_OWNER_INSTANCE_ID,
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
                    # 确有活跃 run（含 queued 僵尸）——业务冲突，提示并返回 run_id 以便前端恢复
                    raise ConflictException(
                        message="当前会话仍在生成",
                        data={"run_id": active.id},
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
            await cls._ensure_started_or_finalize(existing, request, current_user)
            return existing

        await cls._ensure_started_or_finalize(run, request, current_user)
        return run

    @classmethod
    async def _ensure_started_or_finalize(
        cls, run: TAgentRun, request: CreateRunRequest, current_user: CurrentUser
    ) -> None:
        start_task = asyncio.create_task(
            cls._ensure_started(run, request, current_user),
            name=f"agent-run-register:{run.id}",
        )
        try:
            # HTTP 请求取消不能连带取消已提交 run 的注册；shield 后的启动任务会继续完成。
            await asyncio.shield(start_task)
        except asyncio.CancelledError:
            # 确认已提交 run 最终完成注册或被收口，避免丢失后台任务异常。
            try:
                await asyncio.shield(start_task)
            except Exception:
                logger.exception(
                    "agent run detached start failed run_id={} session_id={}",
                    run.id,
                    run.session_id,
                )
                try:
                    await asyncio.shield(cls._finalize_start_failure(run))
                except Exception:
                    logger.exception(
                        "agent run detached start cleanup failed run_id={} session_id={}",
                        run.id,
                        run.session_id,
                    )
            raise
        except Exception as exc:
            logger.exception(
                "agent run start failed after commit run_id={} session_id={}",
                run.id,
                run.session_id,
            )
            try:
                await cls._finalize_start_failure(run)
            except Exception:
                logger.exception(
                    "agent run start failure cleanup failed run_id={} session_id={}",
                    run.id,
                    run.session_id,
                )
            raise ServiceException(message="任务启动失败，请稍后重试") from exc

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
        async with AsyncSessionLocal() as cleanup_db:
            won = await AgentRunRepository(cleanup_db).finalize(
                run_id=run.id,
                target=RunStatus.ERROR,
                assistant_status="error",
                content=content,
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
    async def _ensure_started(
        cls, run: TAgentRun, request: CreateRunRequest, current_user: CurrentUser
    ) -> None:
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
        qa_request = cls._to_qa_request(request, run.qa_type)
        persist_sink = PersistSink(
            checkpoint_interval_seconds=StreamConfig.checkpoint_interval_seconds
        )

        async def producer(publish) -> None:
            async with AsyncSessionLocal() as run_db:
                try:
                    async for line in QaService.exec_query(
                        qa_request,
                        current_user,
                        run_db,
                        assistant_message_id=run.assistant_message_id,
                        messages_precreated=True,
                    ):
                        for event in parse_sse_line_to_event(line):
                            envelope = await cls.publish_projected_event(
                                run.id, projection, event, publish
                            )
                            if envelope is None:
                                continue
                            persist_sink.on_event(event)
                            if persist_sink.should_checkpoint(event):
                                await cls._persist_checkpoint(
                                    run.id,
                                    run.assistant_message_id,
                                    projection,
                                    envelope.sequence,
                                )
                    await cls._persist_projection(run.id, projection)
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
        )
        async with AsyncSessionLocal() as state_db:
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
        projection: RunProjection,
        sequence: int,
    ) -> None:
        deadline = time.monotonic() + StreamConfig.persistence_timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                async with AsyncSessionLocal() as db:
                    content = projection.builder.to_dict()
                    await db.execute(
                        update(TAgentRun)
                        .where(
                            TAgentRun.id == run_id,
                            TAgentRun.status.in_(
                                [status.value for status in {
                                    RunStatus.RUNNING,
                                    RunStatus.RETRYING,
                                    RunStatus.HITL_PENDING,
                                }]
                            ),
                        )
                        .values(
                            last_sequence=sequence,
                            snapshot=projection.persisted_snapshot(),
                            attempt_id=projection.attempt_id,
                            retry_attempt=projection.retry_attempt,
                            retry_max=projection.retry_max,
                            updated_at=_now_ms(),
                        )
                    )
                    await db.execute(
                        update(TChatMessage)
                        .where(
                            TChatMessage.id == assistant_message_id,
                            TChatMessage.status == "streaming",
                        )
                        .values(content=content)
                    )
                    await db.commit()
                return
            except Exception as exc:
                last_error = exc
                run_manager.record_checkpoint_failure(run_id)
                await asyncio.sleep(StreamConfig.persistence_retry_interval_seconds)
        raise RuntimeError("agent run checkpoint persistence timeout") from last_error

    @staticmethod
    def _to_qa_request(request: CreateRunRequest, qa_type: str) -> QaQueryRequest:
        extra = request.extra or {}
        return QaQueryRequest(
            query=request.content,
            qa_type=qa_type,
            chat_id=request.session_id,
            file_dict=extra.get("file_dict") if isinstance(extra.get("file_dict"), dict) else None,
            kb_collections=(
                extra.get("kb_collections") if isinstance(extra.get("kb_collections"), list) else None
            ),
            kb_search_enabled=(
                extra.get("kb_search_enabled")
                if isinstance(extra.get("kb_search_enabled"), bool)
                else None
            ),
            model_id=str(extra["model_id"]) if extra.get("model_id") else None,
            mcp_servers=extra.get("mcp_servers") if isinstance(extra.get("mcp_servers"), list) else None,
            enabled_skills=(
                extra.get("enabled_skills") if isinstance(extra.get("enabled_skills"), list) else None
            ),
            mentions=extra.get("mentions") if isinstance(extra.get("mentions"), list) else None,
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
        async with AsyncSessionLocal() as db:
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
                    snapshot=projection.persisted_snapshot(),
                    finished_at=now,
                    finish_reason=projection.finish_reason or (
                        "server_restart" if target == RunStatus.INTERRUPTED else "stop"
                    ),
                    error_code=projection.error_code,
                    user_error_message=projection.user_error_message,
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
        logger.opt(exception=exc).error(
            "agent run producer failed run_id={} session_id={} status={} exception_type={}",
            run_id,
            projection.session_id,
            projection.status.value,
            type(exc).__name__,
        )
        handle = run_manager.get(run_id)
        limit_error = exc if isinstance(exc, RunLimitExceeded) else handle.limit_error
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
        projection.builder.reconcile_nonterminal_tools(
            ToolState.TIMED_OUT
            if error_code in {"RUN_TIMEOUT", "HITL_TIMEOUT"}
            else ToolState.CANCELLED
            if stopped
            else ToolState.FAILED,
            user_message or "本次工具执行已停止",
        )
        async with AsyncSessionLocal() as db:
            repository = AgentRunRepository(db)
            won = await repository.finalize(
                run_id=run_id,
                target=target,
                assistant_status="partial" if target == RunStatus.PARTIAL else "error",
                content=projection.builder.to_dict(),
                finished_at=_now_ms(),
                finish_reason=reason,
                error_code=error_code,
                user_error_message=user_message,
            )
            if won:
                await db.commit()
            else:
                await db.rollback()
        if handle.status not in {RunStatus.PARTIAL, RunStatus.ERROR}:
            await run_manager.transition(run_id, target)

    @classmethod
    async def get(cls, run_id: str, user_id: str, db: AsyncSession) -> RunSnapshot:
        repository = AgentRunRepository(db)
        row = await repository.get(run_id, user_id)
        if row is None:
            raise NotFoundException(message="任务不存在")
        try:
            handle = run_manager.get(run_id)
        except KeyError:
            parts = row.snapshot.get("parts", []) if isinstance(row.snapshot, dict) else []
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
                retry_attempt=row.retry_attempt,
                retry_max=row.retry_max,
                pending_hitl=pending_hitl,
                updated_at=row.updated_at,
            )
        return handle.snapshot_provider(handle.last_sequence, handle.status, handle.attempt_id)

    @classmethod
    async def stop(cls, run_id: str, user_id: str, db: AsyncSession) -> RunSnapshot:
        row = await AgentRunRepository(db).get(run_id, user_id)
        if row is None:
            raise NotFoundException(message="任务不存在")
        try:
            handle = run_manager.get(run_id)
            if isinstance(handle.state, RunProjection):
                handle.state.cancel_requested = True
                handle.state.builder.mark_running_tools_unknown("已请求停止，工具执行结果无法确认")
            await run_manager.stop(run_id)
        except KeyError:
            pass
        return await cls.get(run_id, user_id, db)

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
        from noesis_server.domain.chat.hitl.pending import PendingHitl

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
        persist_sink = PersistSink(
            checkpoint_interval_seconds=StreamConfig.checkpoint_interval_seconds
        )

        async def producer(publish) -> None:
            async with AsyncSessionLocal() as run_db:
                try:
                    async for line in QaService.exec_hitl_resume(
                        pending=pending,
                        decisions=[item.model_dump(exclude_none=True) for item in request.decisions],
                        grant_scope=request.grant_scope,
                        current_user=current_user,
                        db=run_db,
                        run_managed=True,
                    ):
                        for event in parse_sse_line_to_event(line):
                            envelope = await cls.publish_projected_event(
                                run_id, projection, event, publish
                            )
                            if envelope is None:
                                continue
                            persist_sink.on_event(event)
                            if persist_sink.should_checkpoint(event):
                                await cls._persist_checkpoint(
                                    run_id,
                                    row.assistant_message_id,
                                    projection,
                                    envelope.sequence,
                                )
                    await cls._persist_projection(run_id, projection)
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
        persist_sink = PersistSink(
            checkpoint_interval_seconds=StreamConfig.checkpoint_interval_seconds
        )

        async def producer(publish) -> None:
            async with AsyncSessionLocal() as run_db:
                try:
                    async for line in QaService.exec_test_case_resume(
                        session_id=row.session_id,
                        selected_point_names=request.selected_point_names,
                        current_user=current_user,
                        db=run_db,
                        assistant_message_id=row.assistant_message_id,
                        messages_precreated=True,
                    ):
                        for event in parse_sse_line_to_event(line):
                            envelope = await cls.publish_projected_event(
                                run_id, projection, event, publish
                            )
                            if envelope is None:
                                continue
                            persist_sink.on_event(event)
                            if persist_sink.should_checkpoint(event):
                                await cls._persist_checkpoint(
                                    run_id,
                                    row.assistant_message_id,
                                    projection,
                                    envelope.sequence,
                                )
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
