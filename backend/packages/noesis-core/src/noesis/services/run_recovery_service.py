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
        cls, db: AsyncSession, *, heartbeat_lease_ms: int
    ) -> int:
        """收口僵尸 Run（design §2.2：僵尸判定 = heartbeat 超时，启动/周期对账统一）。

        - ``queued + owner IS NULL``：未被 claim 的排队 Run 存活，由
          dispatcher 补扫启动（enable-distributed-sse-pubsub 决策 2）；
        - **heartbeat 活着的已 claim Run 不碰**——持有它的 worker 仍在执行
          （心跳间隔 = lease/3）。这是周期对账的安全前提：control 每 30s
          扫一轮，活跃 run 必须被跳过；
        - heartbeat 超时（或为 NULL 的 fencing 前遗留行）= 僵尸：未碰世界
          重置 queued 待再认领，已碰世界收口 ``interrupted/server_restart``，
          工具结果标 unknown，不重放。
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
            heartbeat_at = getattr(run, "heartbeat_at", None)
            now = int(time.time() * 1000)
            if heartbeat_at is not None and heartbeat_at >= now - heartbeat_lease_ms:
                continue  # 持有者心跳存活：正常执行中的 run，不是孤儿
            # 字段先固化：后续 UPDATE 落空会使 ORM 属性过期
            run_id = run.id
            run_last_sequence = run.last_sequence
            run_snapshot = run.snapshot if isinstance(run.snapshot, dict) else {}

            # 阶段化重置（worker-role-split）：未碰世界的 run 优先重排队而非
            # 收口。判据安全性——last_sequence=0 即连 message-start 都未发布，
            # 而任何工具执行前必先发布 tool-input 事件，故该状态下工具不可
            # 能执行过（模型调用幂等可重跑）；claim_epoch 保留递增不归零，
            # 超长假死僵尸的旧 epoch 永不等于重置后再认领的新值。
            # snapshot 判据看 parts 内容而非容器：create_run 落库即写
            # ``{"parts": []}`` 骨架，骨架不算碰世界（2026-09-23 实测：
            # 容器 truthy 判定让未启动 run 被误收口 interrupted）。
            if (
                run_last_sequence == 0
                and not run_snapshot.get("parts")
                and isinstance(run.launch_payload, dict)
                and run.launch_payload
            ):
                reset = await db.execute(
                    update(TAgentRun)
                    .where(
                        TAgentRun.id == run_id,
                        TAgentRun.status == run.status,
                        TAgentRun.owner_instance_id == run.owner_instance_id,
                    )
                    .values(
                        status=RunStatus.QUEUED.value,
                        owner_instance_id=None,
                        owner_term=0,
                        heartbeat_at=None,
                        started_at=None,
                        updated_at=now,
                    )
                )
                if reset.rowcount == 1:
                    recovered += 1
                    logger.warning(
                        "启动恢复：run {} 未产出任何事件（{}），重置 queued 等待再认领",
                        run_id,
                        run.status,
                    )
                continue

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
