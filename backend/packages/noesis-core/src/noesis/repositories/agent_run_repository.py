"""Agent run 的 SQLAlchemy repository。"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.chat.runs import ACTIVE_RUN_STATUSES, RunStatus
from noesis.storage.postgres.models.chat import TAgentRun, TChatMessage


class AgentRunRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def add(self, run: TAgentRun) -> None:
        self.db.add(run)
        await self.db.flush()

    async def get(self, run_id: str, user_id: str | None = None) -> TAgentRun | None:
        stmt = select(TAgentRun).where(TAgentRun.id == run_id)
        if user_id is not None:
            stmt = stmt.where(TAgentRun.user_id == user_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_client_request(self, user_id: str, client_request_id: str) -> TAgentRun | None:
        result = await self.db.execute(
            select(TAgentRun).where(
                TAgentRun.user_id == user_id,
                TAgentRun.client_request_id == client_request_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_active_for_session(self, user_id: str, session_id: str) -> TAgentRun | None:
        statuses = [status.value for status in ACTIVE_RUN_STATUSES]
        result = await self.db.execute(
            select(TAgentRun)
            .where(
                TAgentRun.user_id == user_id,
                TAgentRun.session_id == session_id,
                TAgentRun.status.in_(statuses),
            )
            .order_by(TAgentRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def compare_and_set_status(
        self,
        run_id: str,
        expected: Iterable[RunStatus],
        target: RunStatus,
        **values: object,
    ) -> bool:
        expected_values = [status.value for status in expected]
        result = await self.db.execute(
            update(TAgentRun)
            .where(TAgentRun.id == run_id, TAgentRun.status.in_(expected_values))
            .values(status=target.value, **values)
        )
        return result.rowcount == 1

    async def list_non_terminal(self) -> list[TAgentRun]:
        statuses = [status.value for status in ACTIVE_RUN_STATUSES]
        result = await self.db.execute(select(TAgentRun).where(TAgentRun.status.in_(statuses)))
        return list(result.scalars().all())

    async def save_checkpoint(
        self,
        *,
        run_id: str,
        assistant_message_id: str,
        sequence: int,
        snapshot: dict,
        content: dict,
        attempt_id: int,
        retry_attempt: int,
        retry_max: int,
        status: RunStatus,
        finish_reason: str | None,
        updated_at: int,
    ) -> bool:
        """原子写入 checkpoint；迟到 sequence 不得更新 run 或 assistant。"""
        active = [status.value for status in ACTIVE_RUN_STATUSES]
        run_result = await self.db.execute(
            update(TAgentRun)
            .where(
                TAgentRun.id == run_id,
                TAgentRun.status.in_(active),
                TAgentRun.last_sequence <= sequence,
            )
            .values(
                last_sequence=sequence,
                snapshot=snapshot,
                attempt_id=attempt_id,
                retry_attempt=retry_attempt,
                retry_max=retry_max,
                status=status.value,
                finish_reason=finish_reason,
                updated_at=updated_at,
            )
        )
        if run_result.rowcount != 1:
            return False
        message_result = await self.db.execute(
            update(TChatMessage)
            .where(
                TChatMessage.id == assistant_message_id,
                TChatMessage.status == "streaming",
            )
            .values(content=content)
        )
        if message_result.rowcount != 1:
            raise RuntimeError("assistant checkpoint update failed")
        return True

    async def finalize(
        self,
        *,
        run_id: str,
        target: RunStatus,
        assistant_status: str,
        content: dict,
        last_sequence: int,
        finished_at: int,
        finish_reason: str,
        error_code: str | None = None,
        user_error_message: str | None = None,
        snapshot: dict | None = None,
    ) -> bool:
        """同一事务内抢占 run 终态并更新唯一 assistant 行。调用方负责 commit。"""
        if target not in {RunStatus.COMPLETED, RunStatus.PARTIAL, RunStatus.ERROR, RunStatus.INTERRUPTED}:
            raise ValueError(f"target is not terminal: {target.value}")
        active = [status.value for status in ACTIVE_RUN_STATUSES]
        run_result = await self.db.execute(
            update(TAgentRun)
            .where(TAgentRun.id == run_id, TAgentRun.status.in_(active))
            .values(
                status=target.value,
                finish_reason=finish_reason,
                error_code=error_code,
                user_error_message=user_error_message,
                snapshot=snapshot if snapshot is not None else content,
                last_sequence=last_sequence,
                updated_at=finished_at,
                finished_at=finished_at,
            )
        )
        if run_result.rowcount != 1:
            return False

        message_result = await self.db.execute(
            update(TChatMessage)
            .where(
                TChatMessage.id
                == select(TAgentRun.assistant_message_id).where(TAgentRun.id == run_id).scalar_subquery(),
                TChatMessage.status == "streaming",
            )
            .values(
                status=assistant_status,
                content=content,
                extra={
                    "finish_reason": finish_reason,
                    "error_code": error_code,
                    "error": user_error_message,
                },
            )
        )
        if message_result.rowcount != 1:
            raise RuntimeError("assistant terminal compare-and-set failed")
        return True
