"""子 Agent 会话用例。

子 Agent 在产品层是一组普通 ChatSession/ChatMessage/AgentRun，而不是
BackgroundTask 快照。执行器只持有本用例返回的运行身份，所有可见数据由
Postgres 中的标准模型承载。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.chat.message_builder import UserMessageBuilder
from noesis.chat.runs import RunStatus
from noesis.errors.exceptions import ServiceException
from noesis.runtime.logging import logger
from noesis.services.chat_service import ChatService
from noesis.storage.postgres.models.chat import TAgentRun, TChatMessage, TChatSession
from noesis.services.subagent_runtime_port import configure_service_port


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class ChildSessionLaunch:
    session_id: str
    run_id: str
    user_message_id: str
    assistant_message_id: str
    profile_id: str
    created_by_tool_call_id: Optional[str] = None

    def to_dict(self) -> dict[str, str]:
        return {
            "child_session_id": self.session_id,
            "run_id": self.run_id,
            "user_message_id": self.user_message_id,
            "assistant_message_id": self.assistant_message_id,
            "profile_id": self.profile_id,
            "created_by_tool_call_id": self.created_by_tool_call_id,
        }


class SubagentSessionService:
    """创建/更新 child session 的唯一写入边界。"""

    PROFILE_ID = "task-worker"

    @classmethod
    async def reconcile_orphaned_runs(cls, db: AsyncSession) -> int:
        """进程重启后将无法恢复的 child runs 收口为 error。"""
        active = [
            RunStatus.QUEUED.value,
            RunStatus.RUNNING.value,
            RunStatus.RETRYING.value,
            RunStatus.HITL_PENDING.value,
        ]
        now = _now_ms()
        result = await db.execute(
            update(TAgentRun)
            .where(TAgentRun.origin == "subagent", TAgentRun.status.in_(active))
            .values(
                status=RunStatus.ERROR.value,
                error_code="SUBAGENT_PROCESS_RESTARTED",
                user_error_message="后端进程重启，子 Agent 运行已中断",
                finish_reason="process_restart",
                finished_at=now,
                updated_at=now,
            )
        )
        count = int(result.rowcount or 0)
        if count:
            await db.execute(
                update(TChatMessage)
                .where(
                    TChatMessage.id.in_(
                        select(TAgentRun.assistant_message_id).where(
                            TAgentRun.origin == "subagent",
                            TAgentRun.status == RunStatus.ERROR.value,
                            TAgentRun.error_code == "SUBAGENT_PROCESS_RESTARTED",
                        )
                    )
                )
                .values(status="error")
            )
            await db.commit()
        return count

    @classmethod
    async def send_followup(cls, *, session_id: str, user_id: str, message: str) -> dict:
        from noesis.services.subagent_runtime_port import ExecutorPort as BackgroundSubagentExecutor

        task = await cls._owned_child(session_id, user_id)
        if task is None:
            raise ServiceException(message="子会话不存在")
        try:
            BackgroundSubagentExecutor.validate_followup(session_id)
        except ValueError as exc:
            raise ServiceException(message=str(exc)) from exc
        pending_user_message_id = await cls.create_pending_user_message(
            session_id=session_id,
            user_id=user_id,
            message=message,
        )
        try:
            return BackgroundSubagentExecutor.send_message(
                session_id,
                message,
                user_message_id=pending_user_message_id,
            )
        except ValueError as exc:
            await cls._discard_pending_user_message(pending_user_message_id, user_id)
            raise ServiceException(message=str(exc)) from exc

    @staticmethod
    async def _discard_pending_user_message(message_id: str, user_id: str) -> None:
        from noesis.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as db:
            await db.execute(
                update(TChatMessage)
                .where(TChatMessage.id == message_id, TChatMessage.user_id == str(user_id))
                .values(deleted_at=_now_ms())
            )
            await db.commit()

    @classmethod
    async def create_pending_user_message(
        cls,
        *,
        session_id: str,
        user_id: str,
        message: str,
    ) -> str:
        from noesis.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as db:
            session = await ChatService.get_session_by_id(session_id, user_id=user_id, db=db)
            if session is None or session.kind != "subagent":
                raise ServiceException(message="子会话不存在")
            now = _now_ms()
            message_id = str(uuid.uuid4())
            _, sequences = await ChatService.reserve_message_sequences(session_id, user_id, 1, db)
            db.add(TChatMessage(
                id=message_id,
                session_id=session_id,
                parent_id=None,
                user_id=str(user_id),
                role="user",
                content=UserMessageBuilder(message.strip()).to_dict(),
                extra={"origin": "subagent", "pending_run": True},
                status="completed",
                message_sequence=sequences[0],
                created_at=now,
            ))
            session.updated_at = now
            await db.commit()
            return message_id

    @classmethod
    async def resume_hitl(
        cls,
        *,
        run_id: str,
        user_id: str,
        decisions: list[dict],
        db: AsyncSession,
    ) -> TAgentRun:
        run = await cls._get_owned_run(run_id, user_id, db)
        if run is None or run.origin != "subagent":
            raise ServiceException(message="子 Agent run 不存在")
        from noesis.services.subagent_runtime_port import ExecutorPort as BackgroundSubagentExecutor

        try:
            BackgroundSubagentExecutor.submit_decisions(run.session_id, decisions)
        except ValueError as exc:
            raise ServiceException(message=str(exc)) from exc
        return await cls._wait_run(db, run_id, user_id, predicate=lambda row: row.status != RunStatus.HITL_PENDING.value)

    @classmethod
    async def stop_run(cls, *, run_id: str, user_id: str, db: AsyncSession) -> TAgentRun:
        run = await cls._get_owned_run(run_id, user_id, db)
        if run is None or run.origin != "subagent":
            raise ServiceException(message="子 Agent run 不存在")
        from noesis.services.subagent_runtime_port import ExecutorPort as BackgroundSubagentExecutor

        try:
            BackgroundSubagentExecutor.cancel(run.session_id)
        except ValueError as exc:
            raise ServiceException(message=str(exc)) from exc
        return await cls._wait_run(db, run_id, user_id, predicate=lambda row: row.status not in {
            RunStatus.QUEUED.value,
            RunStatus.RUNNING.value,
            RunStatus.HITL_PENDING.value,
        })

    @staticmethod
    async def _get_owned_run(run_id: str, user_id: str, db: AsyncSession) -> TAgentRun | None:
        result = await db.execute(select(TAgentRun).where(TAgentRun.id == run_id, TAgentRun.user_id == str(user_id)))
        return result.scalar_one_or_none()

    @classmethod
    async def _wait_run(cls, db: AsyncSession, run_id: str, user_id: str, predicate) -> TAgentRun:
        import asyncio

        for _ in range(20):
            run = await cls._get_owned_run(run_id, user_id, db)
            if run is None or predicate(run):
                return run
            await asyncio.sleep(0.02)
            await db.rollback()
        return await cls._get_owned_run(run_id, user_id, db)

    @staticmethod
    async def _owned_child(session_id: str, user_id: str):
        from noesis.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as db:
            return await ChatService.get_session_by_id(session_id, user_id=user_id, db=db)

    @staticmethod
    def subscribe_run_events(run_id: str, user_id: str):
        from noesis.services.subagent_runtime_port import ExecutorPort

        return ExecutorPort.subscribe_run_events(run_id, user_id)

    @staticmethod
    def unsubscribe_run_events(run_id: str, queue) -> None:
        from noesis.services.subagent_runtime_port import ExecutorPort

        ExecutorPort.unsubscribe_run_events(run_id, queue)

    @staticmethod
    def get_run_event_history(run_id: str, after_sequence: int = 0) -> list[dict]:
        from noesis.services.subagent_runtime_port import ExecutorPort

        return ExecutorPort.get_run_event_history(run_id, after_sequence)

    @classmethod
    async def launch(
        cls,
        *,
        parent_session_id: str,
        user_id: str,
        description: str,
        tool_call_id: Optional[str] = None,
        db: AsyncSession,
    ) -> ChildSessionLaunch:
        parent = await ChatService.get_session_by_id(parent_session_id, user_id=user_id, db=db)
        if parent is None:
            raise ServiceException(message="父会话不存在")
        parent_run_result = await db.execute(
            select(TAgentRun)
            .where(
                TAgentRun.session_id == parent_session_id,
                TAgentRun.user_id == str(user_id),
                TAgentRun.status.in_([
                    RunStatus.QUEUED.value,
                    RunStatus.RUNNING.value,
                    RunStatus.RETRYING.value,
                    RunStatus.HITL_PENDING.value,
                ]),
            )
            .order_by(TAgentRun.created_at.desc())
            .limit(1)
        )
        parent_run = parent_run_result.scalar_one_or_none()

        now = _now_ms()
        child_session_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        user_message_id = str(uuid.uuid4())
        assistant_message_id = str(uuid.uuid4())
        client_request_id = f"subagent:{run_id}"
        digest = hashlib.sha256(
            json.dumps(
                {"parent_session_id": parent_session_id, "description": description},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        child = TChatSession(
            id=child_session_id,
            parent_id=parent_session_id,
            user_id=str(user_id),
            title=ChatService._normalize_session_title(description) or "子 Agent",
            kind="subagent",
            # 先不写 parent run FK。child session -> parent run -> child run
            # 与 message FK 同时 flush 时会形成循环依赖，必须先让 child 行落库。
            created_by_run_id=None,
            created_by_tool_call_id=tool_call_id,
            extra={
                "qa_type": "SUPER_AGENT_QA",
                "origin": "subagent",
                "agent_profile": cls.PROFILE_ID,
            },
            created_at=now,
            updated_at=now,
            next_message_sequence=3,
        )
        user_message = TChatMessage(
            id=user_message_id,
            session_id=child_session_id,
            parent_id=None,
            user_id=str(user_id),
            role="user",
            content=UserMessageBuilder(description.strip()).to_dict(),
            extra={"origin": "subagent", "run_id": run_id},
            status="completed",
            message_sequence=1,
            created_at=now,
        )
        assistant_message = TChatMessage(
            id=assistant_message_id,
            session_id=child_session_id,
            parent_id=user_message_id,
            user_id=str(user_id),
            role="assistant",
            content={"parts": []},
            extra={"origin": "subagent", "run_id": run_id, "profile_id": cls.PROFILE_ID},
            status="streaming",
            message_sequence=2,
            created_at=now,
        )
        run = TAgentRun(
            id=run_id,
            user_id=str(user_id),
            session_id=child_session_id,
            assistant_message_id=assistant_message_id,
            client_request_id=client_request_id,
            request_digest=digest,
            qa_type="SUPER_AGENT_QA",
            origin="subagent",
            status=RunStatus.QUEUED.value,
            last_sequence=0,
            attempt_id=1,
            snapshot={"parts": []},
            created_at=now,
            updated_at=now,
        )

        db.add(child)
        await db.flush()
        db.add(user_message)
        db.add(assistant_message)
        await db.flush()
        db.add(run)
        await db.flush()
        # child.created_by_run_id 指向创建它的父轮次；child 自身 run 不会伪装成父 run。
        if parent_run is not None:
            await db.execute(
                update(TChatSession)
                .where(TChatSession.id == child_session_id)
                .values(created_by_run_id=parent_run.id)
            )
        await db.execute(
            update(TChatSession)
            .where(TChatSession.id == parent_session_id, TChatSession.user_id == str(user_id))
            .values(updated_at=now)
        )
        await db.commit()
        logger.info(
            "created subagent child session parent={} child={} run={} profile={}",
            parent_session_id,
            child_session_id,
            run_id,
            cls.PROFILE_ID,
        )
        return ChildSessionLaunch(
            session_id=child_session_id,
            run_id=run_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            profile_id=cls.PROFILE_ID,
            created_by_tool_call_id=tool_call_id,
        )

    @classmethod
    async def mark_started(cls, run_id: str, started_at: Optional[int] = None) -> None:
        from noesis.storage.postgres.manager import pg_manager

        now = started_at or _now_ms()
        async with pg_manager.get_async_session_context() as db:
            await db.execute(
                update(TAgentRun)
                .where(TAgentRun.id == run_id, TAgentRun.status == RunStatus.QUEUED.value)
                .values(status=RunStatus.RUNNING.value, started_at=now, updated_at=now)
            )
            await db.commit()

    @classmethod
    async def create_followup_run(
        cls,
        *,
        session_id: str,
        user_id: str,
        message: str,
        user_message_id: Optional[str] = None,
        db: AsyncSession,
    ) -> ChildSessionLaunch:
        """为同一 child session 创建下一轮 user/assistant/run。"""
        session = await ChatService.get_session_by_id(session_id, user_id=user_id, db=db)
        if session is None or session.kind != "subagent":
            raise ServiceException(message="子会话不存在")
        now = _now_ms()
        run_id = str(uuid.uuid4())
        pending_message_id = user_message_id
        user_message_id = pending_message_id or str(uuid.uuid4())
        assistant_message_id = str(uuid.uuid4())
        if pending_message_id:
            _, sequences = await ChatService.reserve_message_sequences(session_id, user_id, 1, db)
            user_message = None
        else:
            _, sequences = await ChatService.reserve_message_sequences(session_id, user_id, 2, db)
            user_message = TChatMessage(
                id=user_message_id,
                session_id=session_id,
                parent_id=None,
                user_id=str(user_id),
                role="user",
                content=UserMessageBuilder(message.strip()).to_dict(),
                extra={"origin": "subagent", "run_id": run_id},
                status="completed",
                message_sequence=sequences[0],
                created_at=now,
            )
        digest = hashlib.sha256(message.encode("utf-8")).hexdigest()
        if user_message is None:
            await db.execute(
                update(TChatMessage)
                .where(TChatMessage.id == user_message_id, TChatMessage.user_id == str(user_id))
                .values(extra={"origin": "subagent", "run_id": run_id}, status="completed")
            )
        assistant_sequence = sequences[0] if pending_message_id else sequences[1]
        assistant_message = TChatMessage(
            id=assistant_message_id,
            session_id=session_id,
            parent_id=user_message_id,
            user_id=str(user_id),
            role="assistant",
            content={"parts": []},
            extra={"origin": "subagent", "run_id": run_id, "profile_id": cls.PROFILE_ID},
            status="streaming",
            message_sequence=assistant_sequence,
            created_at=now,
        )
        run = TAgentRun(
            id=run_id,
            user_id=str(user_id),
            session_id=session_id,
            assistant_message_id=assistant_message_id,
            client_request_id=f"subagent:{run_id}",
            request_digest=digest,
            qa_type="SUPER_AGENT_QA",
            origin="subagent",
            status=RunStatus.QUEUED.value,
            last_sequence=0,
            attempt_id=1,
            snapshot={"parts": []},
            created_at=now,
            updated_at=now,
        )
        if user_message is not None:
            user_message.id = user_message_id
            user_message.extra = {"origin": "subagent", "run_id": run_id}
            db.add(user_message)
        db.add(assistant_message)
        await db.flush()
        db.add(run)
        await db.flush()
        session.updated_at = now
        await db.commit()
        return ChildSessionLaunch(
            session_id=session_id,
            run_id=run_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            profile_id=cls.PROFILE_ID,
            created_by_tool_call_id=None,
        )

    @classmethod
    async def persist_projection(
        cls,
        *,
        run_id: str,
        assistant_message_id: str,
        content: dict,
        sequence: int,
        status: str = "streaming",
    ) -> None:
        """在同一事务更新 assistant 消息和 run 快照。

        executor 的 LangGraph checkpoint 仍用于恢复；产品读取只看这里的标准
        消息。sequence guard 让迟到的跨 loop 投影不能覆盖更新内容。
        """
        from noesis.storage.postgres.manager import pg_manager

        now = _now_ms()
        async with pg_manager.get_async_session_context() as db:
            run_result = await db.execute(
                update(TAgentRun)
                .where(
                    TAgentRun.id == run_id,
                    TAgentRun.last_sequence <= sequence,
                    TAgentRun.status.in_([
                        RunStatus.QUEUED.value,
                        RunStatus.RUNNING.value,
                        RunStatus.HITL_PENDING.value,
                    ]),
                )
                .values(last_sequence=sequence, snapshot=content, updated_at=now)
            )
            if run_result.rowcount != 1:
                await db.rollback()
                return
            await db.execute(
                update(TChatMessage)
                .where(TChatMessage.id == assistant_message_id)
                .values(content=content, status=status)
            )
            await db.commit()

    @classmethod
    async def mark_waiting_approval(
        cls,
        run_id: str,
        interrupt: dict,
        *,
        content: dict,
        sequence: int,
    ) -> None:
        from noesis.storage.postgres.manager import pg_manager

        now = _now_ms()
        snapshot = dict(content)
        snapshot["_pending_hitl"] = interrupt
        async with pg_manager.get_async_session_context() as db:
            await db.execute(
                update(TAgentRun)
                .where(
                    TAgentRun.id == run_id,
                    TAgentRun.last_sequence <= sequence,
                    TAgentRun.status.in_([
                        RunStatus.QUEUED.value,
                        RunStatus.RUNNING.value,
                    ]),
                )
                .values(
                    status=RunStatus.HITL_PENDING.value,
                    last_sequence=sequence,
                    snapshot=snapshot,
                    updated_at=now,
                )
            )
            await db.commit()

    @classmethod
    async def mark_resumed(cls, run_id: str) -> None:
        from noesis.storage.postgres.manager import pg_manager

        now = _now_ms()
        async with pg_manager.get_async_session_context() as db:
            result = await db.execute(select(TAgentRun).where(TAgentRun.id == run_id))
            run = result.scalar_one_or_none()
            snapshot = dict(run.snapshot or {}) if run is not None else {}
            snapshot.pop("_pending_hitl", None)
            await db.execute(
                update(TAgentRun)
                .where(TAgentRun.id == run_id, TAgentRun.status == RunStatus.HITL_PENDING.value)
                .values(
                    status=RunStatus.RUNNING.value,
                    snapshot=snapshot,
                    last_sequence=TAgentRun.last_sequence + 1,
                    updated_at=now,
                )
            )
            await db.commit()

    @classmethod
    async def mark_terminal(
        cls,
        *,
        run_id: str,
        status: RunStatus,
        content: dict,
        error: Optional[str] = None,
        finish_reason: Optional[str] = None,
    ) -> None:
        from noesis.repositories.agent_run_repository import AgentRunRepository
        from noesis.storage.postgres.manager import pg_manager

        if status not in {RunStatus.COMPLETED, RunStatus.ERROR, RunStatus.PARTIAL, RunStatus.INTERRUPTED}:
            raise ValueError(f"终态不合法: {status.value}")
        now = _now_ms()
        async with pg_manager.get_async_session_context() as db:
            run = await AgentRunRepository(db).get(run_id)
            if run is None:
                return
            await AgentRunRepository(db).finalize(
                run_id=run_id,
                target=status,
                assistant_status={
                    RunStatus.COMPLETED: "completed",
                    RunStatus.ERROR: "error",
                    RunStatus.PARTIAL: "partial",
                    RunStatus.INTERRUPTED: "partial",
                }[status],
                content=content,
                last_sequence=int(run.last_sequence or 0),
                snapshot=content,
                finished_at=now,
                finish_reason=finish_reason or status.value,
                error_code="SUBAGENT_FAILED" if error else None,
                user_error_message=error,
            )
            await db.commit()


configure_service_port(SubagentSessionService)

__all__ = ["ChildSessionLaunch", "SubagentSessionService"]
