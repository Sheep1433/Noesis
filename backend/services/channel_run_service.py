"""通道 headless Agent 跑次：无浏览器 SSE，经 RunOrchestrator + PersistSink。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from agent.mcp.loader import load_mcp_tools_by_names
from agent.profiles.super_agent import SuperAgent
from common.logging import logger
from config.database import AsyncSessionLocal
from config.env import LangfuseConfig
from constants.code_enum import IntentEnum
from domain.chat.delivery.orchestrator import RunOrchestrator
from domain.chat.delivery.persist_sink import PersistSink
from domain.chat.delivery.telegram.adapter import extract_plain_text_from_parts
from domain.chat.message_builder import AssistantMessageBuilder, UserMessageBuilder
from domain.chat.streaming.langgraph_sse import LangGraphSseBridge
from services.chat_service import ChatService
from services.user_service import UserService

_orchestrator = RunOrchestrator()
_super_agent = SuperAgent()


@dataclass
class ChannelRunResult:
    session_id: str
    assistant_message_id: str
    plain_text: str
    finish_reason: str
    hitl_pending: bool = False
    hitl_payload: Optional[Dict[str, Any]] = None


async def _headless_stream(
    *,
    agent_generator: Any,
    bridge: LangGraphSseBridge,
    builder: AssistantMessageBuilder,
    ctx: Dict[str, Any],
    session_id: str,
    user_id: str | int,
    qa_type: str,
    origin: str,
    model_name: Optional[str],
    outbound: Optional[Any],
) -> ChannelRunResult:
    from services import qa_service as qs

    sink = PersistSink()
    ctx["_persist_sink"] = sink

    async def on_events(events: List[Any]) -> None:
        for ev in events:
            sink.on_event(ev)
        if outbound is not None:
            try:
                await outbound.feed_events(events)
            except Exception:
                logger.warning(
                    "channel outbound projection failed session_id={}",
                    session_id,
                )
        await qs._persist_stream_checkpoint(
            bridge, session_id, str(user_id)
        )

    try:
        await _orchestrator.run_headless(
            agent_generator,
            bridge=bridge,
            builder=builder,
            ctx=ctx,
            session_id=session_id,
            origin=origin,  # type: ignore[arg-type]
            on_events=on_events,
        )

        if outbound is not None:
            try:
                await outbound.finalize()
            except Exception:
                logger.warning(
                    "channel outbound finalize failed session_id={}", session_id
                )

        decision = sink.final_decision()
        if decision.kind == "hitl_pending" or bridge.last_finish_reason == "hitl_pending":
            await qs._persist_hitl_pending_assistant(
                builder=builder,
                bridge=bridge,
                ctx=ctx,
                session_id=session_id,
                user_id=user_id,
                qa_type=qa_type,
                model=model_name,
            )
            payload = dict(bridge.last_hitl_payload or {})
            plain = (
                extract_plain_text_from_parts(builder.to_dict())
                or "需要审批后继续。"
            )
            return ChannelRunResult(
                session_id=session_id,
                assistant_message_id=bridge.assistant_message_id,
                plain_text=plain,
                finish_reason="hitl_pending",
                hitl_pending=True,
                hitl_payload=payload or None,
            )

        await qs._finalize_streaming_assistant(
            builder=builder,
            bridge=bridge,
            ctx=ctx,
            session_id=session_id,
            user_id=user_id,
            qa_type=qa_type,
            model=model_name,
        )
        plain = extract_plain_text_from_parts(builder.to_dict())
        if not plain:
            plain = "（已完成，无文本回复）"
        return ChannelRunResult(
            session_id=session_id,
            assistant_message_id=bridge.assistant_message_id,
            plain_text=plain,
            finish_reason=bridge.last_finish_reason or decision.finish_reason or "stop",
        )
    finally:
        qs._unregister_active_stream(session_id)


async def run_channel_agent(
    *,
    user_id: str | int,
    session_id: str,
    query: str,
    qa_type: str = IntentEnum.SUPER_AGENT_QA.value[0],
    origin: str = "telegram",
    external_message_id: Optional[str] = None,
    channel_type: str = "telegram",
    outbound: Optional[Any] = None,
) -> ChannelRunResult:
    """
    已配对入站：写 SSOT user 消息 → SuperAgent headless → 终态落库 → 返回纯文本。
    outbound：可选 TelegramOutbound，边跑边伪流式投影。
    """
    text = (query or "").strip()
    if not text:
        raise ValueError("empty query")

    # 仅首期支持 SuperAgent
    if qa_type != IntentEnum.SUPER_AGENT_QA.value[0]:
        qa_type = IntentEnum.SUPER_AGENT_QA.value[0]

    async with AsyncSessionLocal() as db:
        current_user = await UserService._user_from_id(int(user_id), db)
        await ChatService.get_or_create_session(
            user_id=current_user.user_id,
            session_id=session_id,
            title=text[:100],
            extra={"qa_type": qa_type, "origin": origin},
            db=db,
        )
        user_extra: Dict[str, Any] = {
            "qa_type": qa_type,
            "origin": origin,
            "channel_type": channel_type,
        }
        if external_message_id:
            user_extra["external_message_id"] = external_message_id
        await ChatService.save_message(
            session_id=session_id,
            user_id=current_user.user_id,
            role="user",
            content=UserMessageBuilder(content=text).serialize(),
            extra=user_extra,
            db=db,
        )

        from services import qa_service as qs

        resolved_model_id = await qs._resolve_model_for_query(
            session_id=session_id,
            user_id=str(current_user.user_id),
            request_model_id=None,
            db=db,
        )
        resolved_model_name = qs._resolved_model_name(resolved_model_id)
        mcp_server_ids = await qs._resolve_mcp_servers_for_query(
            session_id=session_id,
            user_id=str(current_user.user_id),
            qa_type=qa_type,
            request_mcp_servers=None,
            db=db,
        )
        enabled_skills = await qs._resolve_enabled_skills_for_query(
            session_id=session_id,
            user_id=str(current_user.user_id),
            request_enabled_skills=None,
            db=db,
        )
        mcp_tools: List[Any] = []
        if mcp_server_ids:
            mcp_tools = await load_mcp_tools_by_names(
                mcp_server_ids,
                user_id=str(current_user.user_id),
            )

        agent_generator = _super_agent.run_agent(
            text,
            session_id=session_id,
            current_user=current_user,
            file_list=None,
            qa_type=qa_type,
            model_id=resolved_model_id,
            mcp_tools=mcp_tools or None,
            enabled_skills=enabled_skills,
            db=db,
        )

        bridge = LangGraphSseBridge(
            session_id,
            emit_langfuse_session_hint=LangfuseConfig.langfuse_tracing_enabled,
        )
        builder = AssistantMessageBuilder(
            session_id=session_id,
            message_id=bridge.assistant_message_id,
        )
        ctx = qs._new_stream_ctx()

        if await qs._insert_streaming_assistant_skeleton(
            bridge.assistant_message_id, session_id, current_user.user_id
        ):
            ctx["_assistant_db_id"] = bridge.assistant_message_id

        qs._register_active_stream(
            session_id,
            qs._ActiveStreamState(
                builder=builder,
                ctx=ctx,
                qa_type=qa_type,
                model_name=resolved_model_name,
            ),
        )

        logger.info(
            "channel_run start origin={} session_id={} user_id={} qa_type={}",
            origin,
            session_id,
            current_user.user_id,
            qa_type,
        )
        return await _headless_stream(
            agent_generator=agent_generator,
            bridge=bridge,
            builder=builder,
            ctx=ctx,
            session_id=session_id,
            user_id=current_user.user_id,
            qa_type=qa_type,
            origin=origin,
            model_name=resolved_model_name,
            outbound=outbound,
        )


async def resume_channel_hitl(
    *,
    user_id: str | int,
    session_id: str,
    interrupt_id: str,
    decisions: List[Dict[str, Any]],
    grant_scope: Optional[str] = None,
    origin: str = "telegram",
    outbound: Optional[Any] = None,
) -> ChannelRunResult:
    """Telegram / 通道 HITL resume：对齐网页 decisions，无 SSE。"""
    from domain.chat.hitl.pending import pending_hitl
    from agent.guardrails.session_grants import session_grants
    from domain.chat.hitl.timeout import cancel_hitl_timeout
    from models.chat_models import TChatMessage
    from sqlalchemy import and_, select
    from services import qa_service as qs

    qa_type = IntentEnum.SUPER_AGENT_QA.value[0]

    async with AsyncSessionLocal() as db:
        current_user = await UserService._user_from_id(int(user_id), db)
        pending = pending_hitl.get(session_id)
        if (
            pending is None
            or pending.interrupt_id != interrupt_id
            or pending.user_id != str(current_user.user_id)
        ):
            return ChannelRunResult(
                session_id=session_id,
                assistant_message_id="",
                plain_text="无匹配的待审批请求（可能已处理或已超时）。",
                finish_reason="error",
            )
        if pending_hitl.is_expired(pending):
            pending_hitl.clear(session_id)
            return ChannelRunResult(
                session_id=session_id,
                assistant_message_id=pending.assistant_message_id,
                plain_text="审批已超时。",
                finish_reason="error",
            )

        if grant_scope == "session":
            session_grants.grant(session_id, "network_execute")

        decision_payloads: List[Dict[str, Any]] = []
        for d in decisions:
            item: Dict[str, Any] = {"type": d.get("type")}
            if d.get("message") is not None:
                item["message"] = d["message"]
            decision_payloads.append(item)

        aid = pending.assistant_message_id
        actions = list(pending.action_requests or [])
        pending_hitl.pop_if_match(session_id, interrupt_id)
        cancel_hitl_timeout(session_id)

        existing_content: Dict[str, Any] = {"version": 1, "parts": []}
        async with AsyncSessionLocal() as persist_db:
            result = await persist_db.execute(
                select(TChatMessage).where(
                    and_(
                        TChatMessage.id == aid,
                        TChatMessage.session_id == session_id,
                        TChatMessage.user_id == str(current_user.user_id),
                        TChatMessage.deleted_at.is_(None),
                    )
                )
            )
            msg = result.scalar_one_or_none()
            if msg and isinstance(msg.content, dict):
                existing_content = msg.content

        bridge = LangGraphSseBridge(
            session_id,
            emit_langfuse_session_hint=LangfuseConfig.langfuse_tracing_enabled,
            assistant_message_id=aid,
        )
        builder = AssistantMessageBuilder(session_id=session_id, message_id=aid)
        builder.load_from_content_dict(existing_content)
        ctx = qs._new_stream_ctx()
        ctx["_assistant_db_id"] = aid
        qs._register_active_stream(
            session_id,
            qs._ActiveStreamState(
                builder=builder,
                ctx=ctx,
                qa_type=qa_type,
                model_name=None,
            ),
        )

        for idx, decision in enumerate(decision_payloads):
            action = actions[idx] if idx < len(actions) else {}
            tool_call_id = action.get("tool_call_id")
            name = str(action.get("name") or "")
            dtype = decision.get("type")
            if dtype == "approve":
                builder.update_tool_hitl(
                    tool_call_id,
                    {"status": "approved", "decision": "approve"},
                )
            elif dtype == "reject":
                msg_text = decision.get("message") or "用户拒绝了该操作"
                builder.update_tool_hitl(
                    tool_call_id,
                    {"status": "rejected", "decision": "reject"},
                    status="error",
                )
                try:
                    builder.append_tool_output(
                        name,
                        msg_text,
                        tool_call_id,
                        status="error",
                        error=msg_text,
                        error_category="deterministic",
                    )
                except ValueError:
                    pass
            elif dtype == "respond":
                answer = str(decision.get("message") or "")
                builder.update_tool_hitl(
                    tool_call_id,
                    {"status": "answered", "decision": "respond"},
                    status="success",
                )
                try:
                    builder.append_tool_output(
                        name,
                        answer,
                        tool_call_id,
                        status="success",
                    )
                except ValueError:
                    pass

        agent_generator = _super_agent.resume_agent(
            session_id=session_id,
            decisions=decision_payloads,
            current_user=current_user,
            qa_type=qa_type,
            db=db,
            message_id=aid,
        )

        logger.info(
            "channel_hitl_resume start origin={} session_id={} interrupt_id={}",
            origin,
            session_id,
            interrupt_id,
        )
        return await _headless_stream(
            agent_generator=agent_generator,
            bridge=bridge,
            builder=builder,
            ctx=ctx,
            session_id=session_id,
            user_id=current_user.user_id,
            qa_type=qa_type,
            origin=origin,
            model_name=None,
            outbound=outbound,
        )
