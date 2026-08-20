"""后台子 Agent 终态后的自动续跑（continuation run）。

dsh ``parent.followup(message)`` 的 Noesis run 级等价物：父 Agent 不是常驻
Actor，唤醒 = 用通知消息作为输入自动创建一个新的 run（同 thread_id，
LangGraph checkpointer 保证同会话连续历史）。仅当该会话**无活跃 run** 时
触发；run 活跃期间由 BgNotifyMiddleware 在模型调用边界即时注入。

防环：每会话「无人交互连续自动唤醒」上限（用户真实消息清零计数）——
模型在 continuation run 里再委派新任务仍会在其完成后唤醒（多阶段交付是
期望行为），但无限自延续被上限截断。
"""

from __future__ import annotations

import uuid
from typing import Any

from noesis.agents.subagents import notifications
from noesis.config.env import SubagentConfig
from noesis.errors.exceptions import ConflictException
from noesis.runtime.logging import logger
from noesis.schemas.login_vo import CurrentUser
from noesis.schemas.chat_vo import CreateRunRequest
from noesis.services.run_service import RunService
from noesis.storage.postgres.manager import pg_manager
from noesis.storage.postgres.models.auth import TUser
from noesis.repositories.agent_run_repository import AgentRunRepository
from sqlalchemy import select

CONTINUATION_INSTRUCTION = (
    "以上是后台任务终态通知。用 check_task 收取结果，"
    "继续完成此前对用户承诺的交付（补充摘要/汇报结论）。"
)

# 每会话无人交互连续自动唤醒上限（内存计数；用户真实消息清零）
_MAX_CONSECUTIVE = 5
_wake_counts: dict[str, int] = {}


def note_user_activity(session_id: str) -> None:
    """用户真实消息到达时清零该会话的连续唤醒计数。"""
    _wake_counts.pop(session_id, None)


async def _load_user(user_id: str) -> CurrentUser:
    # t_user.id 为整型；传字符串会被绑成 VARCHAR 与整型列比较报
    # UndefinedFunctionError，先转换（非数字 id 视为无此用户）
    try:
        numeric_id = int(user_id)
    except ValueError:
        return CurrentUser(user_id=user_id, username="")
    async with pg_manager.get_async_session_context() as db:
        result = await db.execute(select(TUser).where(TUser.id == numeric_id))
        user = result.scalar_one_or_none()
        return CurrentUser(
            user_id=user_id,
            username=getattr(user, "username", None) or "",
        )


async def maybe_continue(session_id: str, user_id: str) -> dict[str, Any] | None:
    """尝试自动续跑：无活跃 run + 有未送达通知 + 未超连续唤醒上限。

    返回创建的 run 基本信息；不满足条件返回 None。由 executor 终态钩子
    经 run_on_main_loop 调度到主 loop 执行。
    """
    if not SubagentConfig.auto_continue or not session_id or not user_id:
        return None
    notices = notifications.take_undelivered(session_id, mark_delivered=False)
    if not notices:
        return None
    if _wake_counts.get(session_id, 0) >= _MAX_CONSECUTIVE:
        logger.warning(
            "bg continuation suppressed (consecutive cap) session_id={}", session_id,
        )
        return None

    try:
        current_user = await _load_user(user_id)
        async with pg_manager.get_async_session_context() as db:
            active = await AgentRunRepository(db).get_active_for_session(
                user_id, session_id,
            )
            if active is not None:
                # run 仍活跃：通知留给 BgNotifyMiddleware / 下一轮注入
                return None
            content = f"{notifications.render_block(notices)}\n\n{CONTINUATION_INSTRUCTION}".strip()
            request = CreateRunRequest(
                session_id=session_id,
                content=content,
                client_request_id=f"bgc-{uuid.uuid4()}",
                # source_kind 随 user message extra 落库：前端据此渲染为系统
                # 通知条而非用户气泡（通知不伪装成用户输入）
                extra={"bg_continuation": True, "source_kind": "bg_task_notice"},
            )
            run = await RunService.create(request, current_user, db)
    except ConflictException:
        # 与用户消息并发竞态：放弃唤醒，通知留给既有消费路径
        return None
    except Exception:
        logger.opt(exception=True).error(
            "bg continuation failed session_id={}", session_id,
        )
        return None

    _wake_counts[session_id] = _wake_counts.get(session_id, 0) + 1
    logger.info(
        "bg continuation run created run_id={} session_id={} wake={}/{}",
        run.id, session_id, _wake_counts[session_id], _MAX_CONSECUTIVE,
    )
    # 通知前端附着新 run 的 SSE（页面开着即可实时看到主 Agent 自动续跑）；
    # notice 为本轮通知全文，前端据此在对话流插入系统通知条
    from noesis.agents.subagents.executor import publish_session_event

    publish_session_event(session_id, user_id, {
        "event": "continuation",
        "run_id": run.id,
        "assistant_message_id": run.assistant_message_id,
        "notice": content,
    })
    return {"run_id": run.id, "assistant_message_id": run.assistant_message_id}


def reset_for_tests() -> None:
    _wake_counts.clear()


__all__ = ["maybe_continue", "note_user_activity", "reset_for_tests"]
