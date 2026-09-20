"""t_agent_run_command 仓储：幂等提交、认领与保留清理。

去重窗口 = 保留期（``distributed_runs.command_retention_days``，默认 7 天）：
窗口内相同 ``(user_id, dedupe_key)`` 幂等返回既有命令（HITL decision digest
不同则拒绝）；超窗旧命令由清理删除，重复提交按新命令重验状态。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.storage.postgres.models.agent_run_command import TAgentRunCommand


def _now_ms() -> int:
    return int(time.time() * 1000)


def decision_digest(decision: dict) -> str:
    """HITL 决策摘要：相同决策幂等，不同决策对同一 interrupt 冲突。"""
    canonical = json.dumps(decision or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CommandDeduplicated(Exception):
    """窗口内已存在同键命令（携带既有命令行）。"""

    def __init__(self, existing: TAgentRunCommand) -> None:
        super().__init__("command deduplicated")
        self.existing = existing


class CommandDigestConflict(Exception):
    """同 dedupe_key 但 decision digest 不同（API 映射 409）。"""


class AgentRunCommandRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def submit(
        self,
        *,
        user_id: str,
        command_type: str,
        dedupe_key: str,
        run_id: str | None = None,
        task_id: str | None = None,
        decision_digest_value: str | None = None,
        payload: dict | None = None,
    ) -> TAgentRunCommand:
        """幂等提交：同键同摘要返回既有命令，同键不同摘要抛冲突。"""
        existing = await self._find(user_id, dedupe_key)
        if existing is not None:
            if (
                decision_digest_value is not None
                and existing.decision_digest is not None
                and existing.decision_digest != decision_digest_value
            ):
                raise CommandDigestConflict(dedupe_key)
            return existing
        row = TAgentRunCommand(
            id=str(uuid.uuid4()),
            run_id=run_id,
            task_id=task_id,
            user_id=user_id,
            type=command_type,
            dedupe_key=dedupe_key,
            decision_digest=decision_digest_value,
            payload=payload,
            status="pending",
            created_at=_now_ms(),
        )
        self.db.add(row)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raced = await self._find(user_id, dedupe_key)
            if raced is None:
                raise
            if (
                decision_digest_value is not None
                and raced.decision_digest is not None
                and raced.decision_digest != decision_digest_value
            ):
                raise CommandDigestConflict(dedupe_key) from None
            return raced
        await self.db.refresh(row)
        return row

    async def _find(self, user_id: str, dedupe_key: str) -> TAgentRunCommand | None:
        result = await self.db.execute(
            select(TAgentRunCommand).where(
                TAgentRunCommand.user_id == user_id,
                TAgentRunCommand.dedupe_key == dedupe_key,
            )
        )
        return result.scalar_one_or_none()

    async def get(self, command_id: str) -> TAgentRunCommand | None:
        result = await self.db.execute(
            select(TAgentRunCommand).where(TAgentRunCommand.id == command_id)
        )
        return result.scalar_one_or_none()

    async def claim_pending(self, *, limit: int = 20) -> list[TAgentRunCommand]:
        """认领 pending 命令（FOR UPDATE SKIP LOCKED：多 consumer 安全）。"""
        result = await self.db.execute(
            select(TAgentRunCommand)
            .where(TAgentRunCommand.status == "pending")
            .order_by(TAgentRunCommand.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list(result.scalars().all())
        if rows:
            now = _now_ms()
            await self.db.execute(
                update(TAgentRunCommand)
                .where(TAgentRunCommand.id.in_([r.id for r in rows]))
                .values(status="claimed", claimed_at=now)
            )
            await self.db.commit()
            for row in rows:
                row.status = "claimed"
                row.claimed_at = now
        return rows

    async def mark_terminal(
        self, command_id: str, status: str, summary: str | None = None
    ) -> None:
        await self.db.execute(
            update(TAgentRunCommand)
            .where(TAgentRunCommand.id == command_id)
            .values(status=status, result_summary=summary, completed_at=_now_ms())
        )
        await self.db.commit()

    async def cleanup_expired(self, *, retention_days: float) -> int:
        """删除超保留期的终态命令（保留期 = 幂等去重窗口）。"""
        cutoff = _now_ms() - int(retention_days * 24 * 60 * 60 * 1000)
        result = await self.db.execute(
            delete(TAgentRunCommand).where(
                TAgentRunCommand.status.in_(["completed", "rejected", "no_op"]),
                TAgentRunCommand.completed_at < cutoff,
            )
        )
        await self.db.commit()
        return int(result.rowcount or 0)
