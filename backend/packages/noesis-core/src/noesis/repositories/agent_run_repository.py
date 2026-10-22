"""Agent run 的 SQLAlchemy repository。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.chat.event_mapping.usage_normalize import merge_model_calls, merge_usage
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
        run 标记为 RUN_START_FAILED（排队任务整段对话丢失），必须排除。
        """
        result = await self.db.execute(
            select(TAgentRun)
            .where(
                TAgentRun.status == RunStatus.QUEUED.value,
                TAgentRun.origin != "subagent",
                TAgentRun.owner_instance_id.is_(None),
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
        now_ms: int,
    ) -> int:
        """CAS claim：仅当仍 queued 且未被认领时写入 owner，并递增认领代次。

        返回认领后的 claim_epoch（>0 = 认领成功；0 = 未认领）。epoch 单调
        递增永不归零——对账重置只清 owner/heartbeat，保留 epoch，跨多轮
        重置后旧认领者的 epoch 永不等于当前值（worker-role-split fencing）。
        heartbeat 超时判定只在对账（阶段化分流），claim 永远只见
        ``queued AND owner IS NULL`` 的行（owner_term 是纯审计字段，写入
        惯例置 0，不参与判定）。
        """
        result = await self.db.execute(
            update(TAgentRun)
            .where(
                TAgentRun.id == run_id,
                TAgentRun.status == RunStatus.QUEUED.value,
                TAgentRun.owner_instance_id.is_(None),
            )
            .values(
                owner_instance_id=owner_instance_id,
                owner_term=0,
                claim_epoch=TAgentRun.claim_epoch + 1,
                heartbeat_at=now_ms,
                updated_at=now_ms,
            )
            .returning(TAgentRun.claim_epoch)
        )
        row = result.fetchone()
        return int(row[0]) if row else 0

    async def heartbeat(
        self,
        *,
        run_id: str,
        owner_instance_id: str,
        claim_epoch: int,
        now_ms: int,
    ) -> bool:
        """持有期间的存活心跳：仅命中「本 worker + 本 epoch」的行。

        返回 False = run 已被重置/再认领/终态（epoch 或 owner 不符），
        调用方（worker 心跳协程）应视为失去持有、停掉本地执行。
        """
        result = await self.db.execute(
            update(TAgentRun)
            .where(
                TAgentRun.id == run_id,
                TAgentRun.owner_instance_id == owner_instance_id,
                TAgentRun.claim_epoch == claim_epoch,
                TAgentRun.status.in_([s.value for s in ACTIVE_RUN_STATUSES]),
            )
            .values(heartbeat_at=now_ms)
        )
        return result.rowcount == 1

    async def claim_next_batch(
        self,
        *,
        owner_instance_id: str,
        limit: int,
        now_ms: int,
        capacity_check: Callable[[str], Awaitable[None]] | None = None,
    ) -> list[tuple[str, int]]:
        """worker 批量认领：``FOR UPDATE SKIP LOCKED`` 圈行 + 锁内逐行容量检查 + CAS。

        返回 ``[(run_id, claim_epoch), ...]``（epoch > 0）。SKIP LOCKED 让
        多 worker 同批 queued 时互不阻塞（正确性由行锁 + CAS 双保险，
        SKIP LOCKED 只省竞争空转）。容量检查在锁内调用（调用方注入，
        满则抛异常）：满的行不认领、保持 queued，锁释放后其他 worker
        仍可认领。subagent run 排除（同 list_claimable_queued 语义）。

        调用方负责 commit（认领与启动解耦：本事务提交后启动在事务外）。
        """
        rows = await self.db.execute(
            select(TAgentRun.id, TAgentRun.user_id)
            .where(
                TAgentRun.status == RunStatus.QUEUED.value,
                TAgentRun.origin != "subagent",
                TAgentRun.owner_instance_id.is_(None),
            )
            .order_by(TAgentRun.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        claimed: list[tuple[str, int]] = []
        for run_id, user_id in rows.all():
            if capacity_check is not None:
                try:
                    await capacity_check(str(user_id))
                except Exception:
                    continue  # 本进程容量满（全局或该用户）：留给其他 worker/下轮
            epoch = await self.claim_queued(
                run_id=run_id,
                owner_instance_id=owner_instance_id,
                now_ms=now_ms,
            )
            if epoch > 0:
                claimed.append((run_id, epoch))
        return claimed

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
        claim_epoch: int | None = None,
    ) -> bool:
        """原子写入 checkpoint；迟到 sequence 不得更新 run 或 assistant。

        ``claim_epoch`` 非 None 时为 fencing 条件（worker-role-split）：epoch
        不符（run 已被重置/再认领）的僵尸写在此被拒（rowcount=0 → False）。
        None = 不设防（Phase 2 全调用方接线前的过渡默认）。
        """
        active = [status.value for status in ACTIVE_RUN_STATUSES]
        run_conditions = [
            TAgentRun.id == run_id,
            TAgentRun.status.in_(active),
            TAgentRun.last_sequence <= sequence,
        ]
        if claim_epoch is not None:
            run_conditions.append(TAgentRun.claim_epoch == claim_epoch)
        run_result = await self.db.execute(
            update(TAgentRun)
            .where(*run_conditions)
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

    async def finalize_run_only(
        self,
        *,
        run_id: str,
        target: RunStatus,
        finished_at: int,
        finish_reason: str,
        error_code: str | None = None,
        user_error_message: str | None = None,
        snapshot: dict | None = None,
        last_sequence: int | None = None,
        claim_epoch: int | None = None,
    ) -> bool:
        """仅对 run 行做终态 CAS，不动 assistant 消息行。调用方负责 commit。

        ``claim_epoch`` 非 None 时叠加 fencing 条件（worker-role-split）：
        epoch 不符（被重置/再认领）的僵尸终态写被拒。
        """
        if target not in {
            RunStatus.COMPLETED,
            RunStatus.PARTIAL,
            RunStatus.ERROR,
            RunStatus.INTERRUPTED,
        }:
            raise ValueError(f"target is not terminal: {target.value}")
        active = [status.value for status in ACTIVE_RUN_STATUSES]
        conditions = [TAgentRun.id == run_id, TAgentRun.status.in_(active)]
        if claim_epoch is not None:
            conditions.append(TAgentRun.claim_epoch == claim_epoch)
        run_result = await self.db.execute(
            update(TAgentRun)
            .where(*conditions)
            .values(
                status=target.value,
                finish_reason=finish_reason,
                error_code=error_code,
                user_error_message=user_error_message,
                snapshot=snapshot,
                **({"last_sequence": last_sequence} if last_sequence is not None else {}),
                updated_at=finished_at,
                finished_at=finished_at,
            )
        )
        return run_result.rowcount == 1

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
        claim_epoch: int | None = None,
    ) -> bool:
        """同一事务内抢占 run 终态并更新唯一 assistant 行。调用方负责 commit。"""
        won = await self.finalize_run_only(
            run_id=run_id,
            target=target,
            finished_at=finished_at,
            finish_reason=finish_reason,
            error_code=error_code,
            user_error_message=user_error_message,
            snapshot=snapshot if snapshot is not None else content,
            last_sequence=last_sequence,
            claim_epoch=claim_epoch,
        )
        if not won:
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
            # 各 run 的 projection 只含本段 usage，终态与已落库 usage 累加
            # （merge_usage 单点：与 executor 跨轮累计、chat_service 消息
            # UPDATE 同一语义）。
            message_extra["usage"] = merge_usage(
                old_usage if isinstance(old_usage, dict) else {}, usage,
            )
        # model_calls 与 usage 同一跨 run 累加语义：HITL resume 各 run 只含本段
        # 明细，终态与已落库列表拼接（step 全局重编见 merge_model_calls）。
        if model_calls:
            message_extra["model_calls"] = merge_model_calls(
                old_extra.get("model_calls"), model_calls,
            )
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
