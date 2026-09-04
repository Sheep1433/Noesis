"""启动时收口无法继续执行的 Agent run。"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.runtime.logging import logger
from noesis.chat.runs import RunStatus
from noesis.chat.message_builder import AssistantMessageBuilder
from noesis.repositories.agent_run_repository import AgentRunRepository
from noesis.storage.postgres.models.chat import TAgentDelivery, TAgentRun, TChatMessage


def mark_running_tools_unknown(content: dict[str, Any] | None) -> dict[str, Any]:
    builder = AssistantMessageBuilder()
    builder.load_from_content_dict(content or {"parts": []})
    builder.mark_running_tools_unknown(
        "服务中断，操作结果未确认",
        error_category="server_restart",
    )
    return builder.to_dict()


class RunRecoveryService:
    @classmethod
    async def recover_orphaned_runs(
        cls, db: AsyncSession, *, current_leader_term: int | None = None
    ) -> int:
        """收口上一任期遗留的非终态 Run。

        - ``queued + owner IS NULL``：未被 claim 的排队 Run 存活，由本任
          期 dispatcher 补扫启动（enable-distributed-sse-pubsub 决策 2）；
        - 已 claim/运行中的 Run（owner_term <= current 或无从判断）收口为
          ``interrupted/server_restart``，工具结果标 unknown，不重放。
        """
        repository = AgentRunRepository(db)
        recovered = 0
        for run in await repository.list_non_terminal():
            # 子 Agent run 由 SubagentSessionService.reconcile_orphaned_runs
            # 统一对账（ERROR/SUBAGENT_PROCESS_RESTARTED——executor 状态在进程内，
            # 重启即不可恢复）；此处收口会与其终态语义按调用顺序隐式切分。
            if run.origin == "subagent":
                continue
            if run.status == RunStatus.QUEUED.value and not run.owner_instance_id:
                continue  # 未 claim 的 queued Run 跨重启存活，交给 dispatcher
            if (
                current_leader_term is not None
                and run.owner_term >= current_leader_term
            ):
                # 本任期 claim 的 Run：新 leader 上任时不可能出现（本方法只在
                # 启动早期执行），防御性跳过避免误杀刚 claim 的行
                continue
            # 字段先固化：后续 UPDATE 落空会使 ORM 属性过期
            run_id = run.id
            run_last_sequence = run.last_sequence
            run_snapshot = run.snapshot if isinstance(run.snapshot, dict) else {}
            now = int(time.time() * 1000)
            terminal = dict(
                target=RunStatus.INTERRUPTED,
                finished_at=now,
                finish_reason="server_restart",
                error_code="SERVER_RESTART",
                user_error_message="服务重启，本轮已中断",
            )
            message_result = await db.execute(
                select(TChatMessage).where(TChatMessage.id == run.assistant_message_id)
            )
            message = message_result.scalar_one_or_none()
            if message is None or message.status != "streaming":
                # 历史脏数据（消息终态写入方未同步收口 run，如 automation/channel
                # 链路）：仅收口 run 行、不动消息，下次启动不再重复对账。启动
                # 对账持有 leader 锁且先于 dispatcher/scheduler/channel 启动，
                # SELECT 即权威，无需 CAS 兜底。
                finalized = await repository.finalize_run_only(
                    run_id=run_id,
                    snapshot=run_snapshot,
                    last_sequence=run_last_sequence,
                    **terminal,
                )
                if finalized:
                    logger.warning(
                        "启动恢复：run {} 的 assistant 消息已终态或缺失，仅收口 run 行 "
                        "(消息终态写入方未同步收口 run，属历史脏数据)",
                        run_id,
                    )
            else:
                content = mark_running_tools_unknown(
                    message.content if isinstance(message.content, dict) else run_snapshot
                )
                finalized = await repository.finalize(
                    run_id=run_id,
                    assistant_status="partial",
                    content=content,
                    last_sequence=run_last_sequence,
                    snapshot=content,
                    **terminal,
                )
            if finalized:
                await db.execute(
                    TAgentDelivery.__table__.update()
                    .where(
                        TAgentDelivery.run_id == run_id,
                        TAgentDelivery.status == "running",
                    )
                    .values(
                        status="lost",
                        error_code="SERVER_RESTART",
                        error_message="服务重启，平台发送状态无法确认",
                        updated_at=now,
                        finished_at=now,
                    )
                )
                recovered += 1

        orphan_result = await db.execute(
            select(TChatMessage).where(
                TChatMessage.role == "assistant",
                TChatMessage.status == "streaming",
                ~exists(
                    select(TAgentRun.id).where(
                        TAgentRun.assistant_message_id == TChatMessage.id
                    )
                ),
            )
        )
        recovered_messages = 0
        for message in orphan_result.scalars().all():
            content = mark_running_tools_unknown(
                message.content if isinstance(message.content, dict) else None
            )
            extra = dict(message.extra) if isinstance(message.extra, dict) else {}
            extra.update(
                {
                    "finish_reason": "server_restart",
                    "error_code": "SERVER_RESTART",
                    "error": "服务重启，本轮已中断",
                }
            )
            result = await db.execute(
                update(TChatMessage)
                .where(
                    TChatMessage.id == message.id,
                    TChatMessage.status == "streaming",
                )
                .values(status="partial", content=content, extra=extra)
            )
            if result.rowcount == 1:
                recovered_messages += 1
        await db.commit()
        if recovered or recovered_messages:
            logger.warning(
                "启动时收口悬空 Agent run_count={} orphan_message_count={}",
                recovered,
                recovered_messages,
            )
        return recovered + recovered_messages
