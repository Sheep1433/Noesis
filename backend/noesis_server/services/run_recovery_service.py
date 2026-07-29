"""启动时收口无法继续执行的 Agent run。"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.runtime.logging import logger
from noesis_server.domain.chat.runs import RunStatus
from noesis_server.domain.chat.message_builder import AssistantMessageBuilder
from noesis_server.infrastructure.database.repositories.agent_run import AgentRunRepository
from noesis_server.models.chat_models import TAgentDelivery, TAgentRun, TChatMessage


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
    async def recover_orphaned_runs(cls, db: AsyncSession) -> int:
        repository = AgentRunRepository(db)
        recovered = 0
        for run in await repository.list_non_terminal():
            message_result = await db.execute(
                select(TChatMessage).where(TChatMessage.id == run.assistant_message_id)
            )
            message = message_result.scalar_one_or_none()
            content = mark_running_tools_unknown(
                message.content if message is not None and isinstance(message.content, dict) else run.snapshot
            )
            finalized = await repository.finalize(
                run_id=run.id,
                target=RunStatus.INTERRUPTED,
                assistant_status="partial",
                content=content,
                finished_at=int(time.time() * 1000),
                finish_reason="server_restart",
                error_code="SERVER_RESTART",
                user_error_message="服务重启，本轮已中断",
                snapshot=content,
            )
            if finalized:
                await db.execute(
                    TAgentDelivery.__table__.update()
                    .where(
                        TAgentDelivery.run_id == run.id,
                        TAgentDelivery.status == "running",
                    )
                    .values(
                        status="lost",
                        error_code="SERVER_RESTART",
                        error_message="服务重启，平台发送状态无法确认",
                        updated_at=int(time.time() * 1000),
                        finished_at=int(time.time() * 1000),
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
