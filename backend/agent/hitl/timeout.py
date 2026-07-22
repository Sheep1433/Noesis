"""HITL 超时：无 resume 时按 reject 恢复并终态落库。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from agent.hitl.pending import PendingHitl, pending_hitl
from common.logging import logger


_timeout_tasks: dict[str, asyncio.Task] = {}


def schedule_hitl_timeout(pending: PendingHitl) -> None:
    """为 pending HITL 调度超时 reject（幂等替换同 session 旧任务）。"""
    sid = pending.session_id
    old = _timeout_tasks.pop(sid, None)
    if old and not old.done():
        old.cancel()

    delay = max(0.0, float(pending.expires_at) - time.time())

    async def _run() -> None:
        try:
            await asyncio.sleep(delay)
            cur = pending_hitl.get(sid)
            if cur is None or cur.interrupt_id != pending.interrupt_id:
                return
            if not pending_hitl.is_expired(cur):
                return
            logger.info(
                f"HITL 超时自动 reject session_id={sid} interrupt_id={cur.interrupt_id}"
            )
            await _timeout_reject(cur)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception(f"HITL 超时任务异常 session_id={sid}")
        finally:
            _timeout_tasks.pop(sid, None)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _timeout_tasks[sid] = loop.create_task(_run())


def cancel_hitl_timeout(session_id: str) -> None:
    task = _timeout_tasks.pop(session_id, None)
    if task and not task.done():
        task.cancel()


async def _timeout_reject(pending: PendingHitl) -> None:
    """超时：Command(resume=reject) + assistant 终态 partial。"""
    from agent.hitl.pending import pending_hitl as store
    from agent.super_agent import SuperAgent
    from config.database import AsyncSessionLocal
    from domain.chat.message_builder import AssistantMessageBuilder
    from models.chat_models import TChatMessage
    from services.chat_service import ChatService
    from sqlalchemy import and_, select

    popped = store.pop_if_match(pending.session_id, pending.interrupt_id)
    if popped is None:
        return

    decisions = [{"type": "reject", "message": "HITL 等待超时，已自动拒绝"} for _ in pending.action_requests]
    if not decisions:
        decisions = [{"type": "reject", "message": "HITL 等待超时，已自动拒绝"}]

    agent = SuperAgent()
    # 尽力 resume 图状态，忽略流输出
    try:
        async for _ in agent.resume_agent(
            session_id=pending.session_id,
            decisions=decisions,
            current_user=type("U", (), {"user_id": pending.user_id})(),
            message_id=pending.assistant_message_id,
        ):
            pass
    except Exception:
        logger.exception(
            f"HITL 超时 resume 图失败 session_id={pending.session_id}"
        )

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(TChatMessage).where(
                and_(
                    TChatMessage.id == pending.assistant_message_id,
                    TChatMessage.session_id == pending.session_id,
                    TChatMessage.user_id == pending.user_id,
                    TChatMessage.deleted_at.is_(None),
                )
            )
        )
        msg = result.scalar_one_or_none()
        content: dict[str, Any] = msg.content if msg and isinstance(msg.content, dict) else {"parts": []}
        builder = AssistantMessageBuilder(
            session_id=pending.session_id,
            message_id=pending.assistant_message_id,
        )
        builder.load_from_content_dict(content)
        for action in pending.action_requests:
            tcid = action.get("tool_call_id")
            builder.update_tool_hitl(
                tcid,
                {"status": "rejected", "decision": "reject", "reason": "timeout"},
                status="error",
            )
        extra = dict(msg.extra) if msg and isinstance(msg.extra, dict) else {}
        extra["finish_reason"] = "error"
        extra["error_message"] = "HITL 等待超时，已自动拒绝"
        await ChatService.update_assistant_message(
            message_id=pending.assistant_message_id,
            session_id=pending.session_id,
            user_id=pending.user_id,
            content=builder.to_dict(),
            status="partial",
            extra=extra,
            db=db,
        )
