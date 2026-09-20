"""追加消息命令化 + shell 事实行真实 PG 集成测试（integration 标记）。

覆盖 tasks：bg-task-message-command 4.3（受理事务原子性 / dedupe / 租约重置）、
bg-task-durable-facts 7.2/7.3（shell 行生命周期、pending 行翻转的真实 DB 语义）。
借库中真实 subagent 子会话承载外键；测试数据用后即清。
"""
from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import delete, insert, select

pytestmark = pytest.mark.integration

from noesis.services.bg_shell_job_service import BgShellJobService
from noesis.services.subagent_session_service import SubagentSessionService
from noesis.storage.postgres.manager import pg_manager
from noesis.storage.postgres.models.agent_run_command import TAgentRunCommand
from noesis.storage.postgres.models.bg_task import TBgShellJob
from noesis.storage.postgres.models.chat import TChatMessage, TChatSession


async def _borrow_subagent_session() -> tuple[str, str]:
    async with pg_manager.get_async_session_context() as db:
        session = (
            await db.execute(
                select(TChatSession)
                .where(TChatSession.kind == "subagent", TChatSession.deleted_at.is_(None))
                .order_by(TChatSession.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    assert session is not None, "库中无 subagent 子会话可借用"
    return str(session.id), str(session.user_id)


async def _cleanup_messages(session_id: str, message_ids: list[str]) -> None:
    async with pg_manager.get_async_session_context() as db:
        await db.execute(delete(TChatMessage).where(TChatMessage.id.in_(message_ids)))
        await db.commit()


@pytest.mark.asyncio
async def test_bg_task_command_real_db() -> None:
    """三个场景共用单事件循环（pg 连接池绑定 loop，跨测试复用会炸）。"""
    # ---- 场景 1：shell 事实行生命周期 ----
    task_id = f"bg-itest-{uuid.uuid4().hex[:8]}"
    try:
        await BgShellJobService.persist_start(
            task_id=task_id, session_id="s-itest", user_id=str(uuid.uuid4()),
            command="echo hi", status="running",
        )
        row = await BgShellJobService.get_task(task_id)
        assert row is not None and row["status"] == "running"
        await BgShellJobService.mark_terminal(
            task_id=task_id, status="completed",
            error=None, result_tail="exit code: 0\nhi", completed_at=None,
        )
        row = await BgShellJobService.get_task(task_id)
        assert row["status"] == "completed" and "hi" in (row["result"] or "")
        # 终态不可被晚到规格覆写
        await BgShellJobService.mark_terminal(
            task_id=task_id, status="failed", error="late",
            result_tail=None, completed_at=None,
        )
        assert (await BgShellJobService.get_task(task_id))["status"] == "completed"
    finally:
        async with pg_manager.get_async_session_context() as db:
            await db.execute(delete(TBgShellJob).where(TBgShellJob.task_id == task_id))
            await db.commit()


    # ---- 场景 2：受理事务（pending 行 + 命令行原子）----
    # 直接调 _accept_write（不等待）：外部旧实例的消费者可能干扰 wait 结果
    session_id, user_id = await _borrow_subagent_session()
    try:
        projection = await SubagentSessionService.db_task_projection(session_id)
        child_session_id, command_id = await SubagentSessionService._accept_write_row_and_command(
            task_ref=session_id, user_id=user_id, message="集成验证追加",
            model_id=None, reasoning_effort=None, projection=projection,
        )
        assert child_session_id == session_id
        # 行与命令都真实落库
        assert await SubagentSessionService.count_pending_messages(session_id) >= 1
        async with pg_manager.get_async_session_context() as db:
            rows = (
                await db.execute(
                    select(TAgentRunCommand).where(
                        TAgentRunCommand.task_id == session_id,
                        TAgentRunCommand.type == "bg_task_deliver",
                    )
                )
            ).scalars().all()
        assert rows and rows[0].id == command_id, "命令行必须落库且 id 一致"
        payload = rows[0].payload or {}
        assert payload.get("child_session_id") == session_id
        assert str(payload.get("message_id") or "").count("-") == 4
        # dedupe：同键重复提交返回同一命令（幂等）
        from noesis.repositories.agent_run_command_repository import (
            AgentRunCommandRepository,
        )

        async with pg_manager.get_async_session_context() as db:
            again = await AgentRunCommandRepository(db).submit(
                user_id=user_id, command_type="bg_task_deliver",
                dedupe_key=f"bg:{session_id}:deliver:{payload.get('message_id')}",
                task_id=session_id, flush=True,
                payload={"child_session_id": session_id, "message_id": payload.get("message_id")},
            )
        assert again.id == rows[0].id
    finally:
        async with pg_manager.get_async_session_context() as db:
            await db.execute(
                delete(TAgentRunCommand).where(TAgentRunCommand.task_id == session_id)
            )
            await db.execute(
                delete(TChatMessage).where(
                    TChatMessage.session_id == session_id,
                    TChatMessage.extra["pending_run"].as_string().isnot(None),
                )
            )
            await db.commit()


    # ---- 场景 3：pending 翻转与认领租约重置 ----
    session_id, user_id = await _borrow_subagent_session()
    now = int(time.time() * 1000)
    mid = str(uuid.uuid4())
    cmd_id = str(uuid.uuid4())
    try:
        async with pg_manager.get_async_session_context() as db:
            await db.execute(insert(TChatMessage).values(
                id=mid, session_id=session_id, user_id=user_id, role="user",
                content={"parts": [{"type": "text", "content": "继续"}]},
                extra={"origin": "subagent", "pending_run": True},
                message_sequence=99000, created_at=now,
            ))
            await db.execute(insert(TAgentRunCommand).values(
                id=cmd_id, task_id=session_id, user_id=user_id,
                type="bg_task_deliver", dedupe_key=f"itest:{cmd_id}",
                payload={"child_session_id": session_id, "message_id": mid},
                status="claimed", claimed_at=now - 10 * 60 * 1000,  # 租约早已超时
                created_at=now,
            ))
            await db.commit()

        assert await SubagentSessionService.count_pending_messages(session_id) >= 1
        # 认领租约：超时 claimed 重置回 pending
        from noesis.repositories.agent_run_command_repository import (
            AgentRunCommandRepository,
        )

        async with pg_manager.get_async_session_context() as db:
            reset = await AgentRunCommandRepository(db).reset_stale_claimed(lease_ms=60_000)
        assert reset >= 1
        # 消费拒绝：翻转 dropped + 计数回落
        flipped = await SubagentSessionService.flip_pending_message_dropped(mid)
        assert flipped == 1
        before = await SubagentSessionService.count_pending_messages(session_id)
        await SubagentSessionService.flip_pending_messages_dropped(session_id)
        assert await SubagentSessionService.count_pending_messages(session_id) == before
    finally:
        async with pg_manager.get_async_session_context() as db:
            await db.execute(delete(TChatMessage).where(TChatMessage.id == mid))
            await db.execute(delete(TAgentRunCommand).where(TAgentRunCommand.id == cmd_id))
            await db.commit()
