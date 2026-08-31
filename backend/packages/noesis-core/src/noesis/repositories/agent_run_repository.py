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

    async def get_by_client_request(
        self, user_id: str, client_request_id: str
    ) -> TAgentRun | None:
        result = await self.db.execute(
            select(TAgentRun).where(
                TAgentRun.user_id == user_id,
                TAgentRun.client_request_id == client_request_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_active_runs_for_user(self, user_id: str) -> list[TAgentRun]:
        """用户的全部活跃 run（会话列表信令流首帧对齐用）。"""
        statuses = [status.value for status in ACTIVE_RUN_STATUSES]
        result = await self.db.execute(
            select(TAgentRun)
            .where(
                TAgentRun.user_id == user_id,
                TAgentRun.status.in_(statuses),
            )
            .order_by(TAgentRun.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_active_for_session(
        self, user_id: str, session_id: str
    ) -> TAgentRun | None:
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

    async def get_latest_for_session(
        self, user_id: str, session_id: str
    ) -> TAgentRun | None:
        result = await self.db.execute(
            select(TAgentRun)
            .where(TAgentRun.user_id == user_id, TAgentRun.session_id == session_id)
            .order_by(TAgentRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_run_times_for_session(
        self, session_id: str
    ) -> dict[str, tuple[int | None, int | None]]:
        """assistant_message_id → (started_at, finished_at)。

        一条 session 维度查询（idx_agent_run_session_status 前缀命中），
        供消息列表合并 run 生命周期时间——消息表的 updated_at 是 checkpoint
        落库时间，会被持续刷新，不能当"本轮完成时间"用。
        """
        result = await self.db.execute(
            select(
                TAgentRun.assistant_message_id,
                TAgentRun.started_at,
                TAgentRun.finished_at,
            ).where(TAgentRun.session_id == session_id)
        )
        return {
            row.assistant_message_id: (row.started_at, row.finished_at)
            for row in result.all()
        }

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
        result = await self.db.execute(
            select(TAgentRun).where(TAgentRun.status.in_(statuses))
        )
        return list(result.scalars().all())

    async def list_claimable_queued(self, *, limit: int = 20) -> list[TAgentRun]:
        """可 claim 的 queued Run：未被任何实例认领（owner IS NULL 且未写入 term）。

        subagent run 由进程内 executor 调度（mark_started / mark_terminal），
        且不写 launch_payload——dispatcher claim 后重建上下文必然失败并把
        run 收口成 RUN_START_FAILED（排队任务整段对话丢失），必须排除。
        """
        result = await self.db.execute(
            select(TAgentRun)
            .where(
                TAgentRun.status == RunStatus.QUEUED.value,
                TAgentRun.origin != "subagent",
                TAgentRun.owner_instance_id.is_(None),
                TAgentRun.owner_term == 0,
            )
            .order_by(TAgentRun.created_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def claim_queued(
        self,
        *,
        run_id: str,
        owner_instance_id: str,
        owner_term: int,
        now_ms: int,
    ) -> bool:
        """CAS claim：仅当仍 queued 且未被认领时写入 owner 与 term。

        leader 任期有效性由调用方（dispatcher 的 leadership token）在进程内
        校验；owner_term 落库供重启 recovery 区分「本任期内 claim」与
        「旧任期残留」。
        """
        result = await self.db.execute(
            update(TAgentRun)
            .where(
                TAgentRun.id == run_id,
                TAgentRun.status == RunStatus.QUEUED.value,
                TAgentRun.owner_instance_id.is_(None),
                TAgentRun.owner_term == 0,
            )
            .values(
                owner_instance_id=owner_instance_id,
                owner_term=owner_term,
                updated_at=now_ms,
            )
        )
        return result.rowcount == 1

    async def save_checkpoint(
        self,
        *,
        run_id: str,
        assistant_message_id: str,
        sequence: int,
        snapshot: dict,
        content: dict,
        attempt_id: int,
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
        usage: dict | None = None,
        model_calls: list | None = None,
    ) -> bool:
        """同一事务内抢占 run 终态并更新唯一 assistant 行。调用方负责 commit。"""
        if target not in {
            RunStatus.COMPLETED,
            RunStatus.PARTIAL,
            RunStatus.ERROR,
            RunStatus.INTERRUPTED,
        }:
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

        # 合并语义（与 chat_service.update_assistant_message 对齐）：旧键保留、
        # 新键覆盖；usage 累加——HITL resume 同一 assistant 消息跨多个 run，
        # 各 run 的 projection 只含本段 usage，终态必须与已落库 usage 累加。
        message_id_row = await self.db.execute(
            select(TChatMessage.extra).where(
                TChatMessage.id
                == select(TAgentRun.assistant_message_id)
                .where(TAgentRun.id == run_id)
                .scalar_subquery(),
                TChatMessage.status == "streaming",
            )
        )
        old_extra_row = message_id_row.fetchone()
        old_extra = (
            dict(old_extra_row[0])
            if old_extra_row and isinstance(old_extra_row[0], dict)
            else {}
        )

        message_extra: dict = {
            **old_extra,
            "finish_reason": finish_reason,
            "error_code": error_code,
            "error": user_error_message,
        }
        if usage and usage.get("steps"):
            old_usage = old_extra.get("usage")
            if isinstance(old_usage, dict):
                message_extra["usage"] = {
                    key: float(old_usage.get(key) or 0) + float(usage.get(key) or 0)
                    for key in usage
                }
            else:
                message_extra["usage"] = dict(usage)
        # model_calls 与 usage 同一跨 run 累加语义：HITL resume 各 run 只含本段
        # 明细，终态与已落库列表拼接。追加段 step 重编为全局连续序号——
        # 各 run 独立从 1 计数，直接拼接会重复（usage.steps 是数值累加无此问题）。
        if model_calls:
            old_calls = old_extra.get("model_calls")
            base = list(old_calls) if isinstance(old_calls, list) else []
            offset = len(base)
            message_extra["model_calls"] = base + [
                {**call, "step": offset + i + 1}
                for i, call in enumerate(model_calls)
                if isinstance(call, dict)
            ]
        message_result = await self.db.execute(
            update(TChatMessage)
            .where(
                TChatMessage.id
                == select(TAgentRun.assistant_message_id)
                .where(TAgentRun.id == run_id)
                .scalar_subquery(),
                TChatMessage.status == "streaming",
            )
            .values(
                status=assistant_status,
                content=content,
                extra=message_extra,
            )
        )
        if message_result.rowcount != 1:
            raise RuntimeError("assistant terminal compare-and-set failed")
        return True
