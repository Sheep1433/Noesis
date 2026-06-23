"""会话级 Agent run 取消（删 session 前调用）。"""

from __future__ import annotations

from common.logging import logger


async def cancel_session_agent_runs(session_id: str) -> None:
    """取消各 Agent 在 session_id 上的进行中 run（幂等）。"""
    from services.qa_service import (
        case_coordinator,
        common_agent,
        deep_research_agent,
        fault_agent,
    )

    await common_agent.cancel_task(session_id)
    await fault_agent.cancel_task(session_id)
    await deep_research_agent.cancel_task(session_id)
    await case_coordinator.cancel_task(session_id)
    logger.info("已请求取消 session Agent runs session_id=%s", session_id)
