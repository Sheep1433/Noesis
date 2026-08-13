"""通道 headless Agent 跑次：无浏览器 SSE，共用 typed Run 管线。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import time
import uuid
from typing import Any, Dict, List, Optional

from noesis.agents.mcp.loader import load_mcp_tools_by_names
from noesis.agents.super_agent import SuperAgent
from noesis.runtime.logging import logger
from noesis.storage.postgres.manager import pg_manager
from noesis.config.env import LangfuseConfig, StreamConfig
from noesis.config.code_enum import IntentEnum
from noesis.services.persist_sink import PersistSink
from noesis.chat.delivery.channel_worker import ChannelDeliveryWorker
from noesis.chat.message_builder import AssistantMessageBuilder, UserMessageBuilder
from noesis.chat.tool_state import ToolState
from noesis.chat.event_mapping.langgraph_bridge import LangGraphSseBridge
from noesis.services.chat_service import ChatService
from noesis.services.user_service import UserService
from noesis.storage.postgres.models.chat import TAgentDelivery, TAgentRun, TChatMessage
from noesis.repositories.agent_run_repository import AgentRunRepository
from noesis.services.run_service import RunProjection, RunService, run_manager
from noesis.chat.runs import RunLimitExceeded, RunStatus

_super_agent = SuperAgent()


def _plain_text(parts: Dict[str, Any]) -> str:
    from noesis.chat.delivery.telegram.adapter import extract_plain_text_from_parts

    return extract_plain_text_from_parts(parts)


@dataclass
class ChannelRunResult:
    session_id: str
    assistant_message_id: str
    plain_text: str
    finish_reason: str
    hitl_pending: bool = False
    hitl_payload: Optional[Dict[str, Any]] = None


async def _set_delivery_result(
    delivery_id: Optional[str], status: str, *, error_code: Optional[str] = None
) -> None:
    if delivery_id is None:
        return
    now = int(time.time() * 1000)
    async with pg_manager.get_async_session_context() as delivery_db:
        await delivery_db.execute(
            TAgentDelivery.__table__.update()
            .where(TAgentDelivery.id == delivery_id)
            .values(
                status=status,
                error_code=error_code,
                error_message="平台发送失败" if error_code else None,
                attempts=TAgentDelivery.attempts + 1,
                updated_at=now,
                finished_at=now if status in {"completed", "error", "lost"} else None,
            )
        )
        await delivery_db.commit()


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
    publish: Optional[Any] = None,
    projection: Optional[RunProjection] = None,
    run_id: Optional[str] = None,
    delivery_id: Optional[str] = None,
) -> ChannelRunResult:
    from noesis.services.qa import helpers as qs

    sink = PersistSink()
    ctx["_persist_sink"] = sink

    async def record_delivery(status: str, *, error_code: Optional[str] = None) -> None:
        await _set_delivery_result(delivery_id, status, error_code=error_code)

    async def delivery_failed(error_code: str) -> None:
        logger.warning(
            "channel delivery failed run_id={} delivery_id={} session_id={} error_code={}",
            run_id,
            delivery_id,
            session_id,
            error_code,
        )
        await record_delivery("error", error_code=error_code)

    delivery_worker = (
        ChannelDeliveryWorker(
            outbound,
            max_batches=StreamConfig.run_channel_queue_max_batches,
            max_bytes=StreamConfig.run_channel_queue_max_bytes,
            drain_seconds=StreamConfig.run_channel_drain_seconds,
            on_failure=delivery_failed,
        )
        if outbound is not None
        else None
    )
    managed_deliveries = publish is not None and run_id is not None

    async def channel_delivery(envelope) -> None:
        if delivery_worker is not None:
            await delivery_worker.submit([envelope.event])

    if managed_deliveries:
        if delivery_worker is not None:
            await run_manager.register_delivery(
                run_id, f"channel:{delivery_id or origin}", channel_delivery
            )

    async def on_events(events: List[Any]) -> None:
        for ev in events:
            if projection is not None and publish is not None and run_id is not None:
                await RunService.publish_projected_event(run_id, projection, ev, publish)
            else:
                sink.on_event(ev)
                if projection is not None:
                    projection.apply(ev)
                if publish is not None:
                    await publish(ev, projection.attempt_id if projection is not None else None)
        if delivery_worker is not None and not managed_deliveries:
            await delivery_worker.submit(events)
        if not managed_deliveries:
            await qs._persist_stream_checkpoint(bridge, session_id, str(user_id))

    try:
        async for event in qs._yield_run_events_from_agent(
            agent_generator,
            bridge=bridge,
            builder=builder,
            ctx=ctx,
            session_id=session_id,
            user_id=str(user_id),
            qa_type=qa_type,
            persist_sink=sink,
        ):
            await on_events([event])
        async for event in qs._finalize_run_events(
            bridge, ctx, session_id, str(user_id)
        ):
            await on_events([event])

        if managed_deliveries:
            await run_manager.drain_persistence(run_id)
            if delivery_worker is not None:
                await run_manager.drain_delivery(run_id, f"channel:{delivery_id or origin}")

        if delivery_worker is not None:
            if await delivery_worker.finalize():
                await record_delivery("completed")

        decision = sink.final_decision()
        if decision.kind == "hitl_pending" or bridge.last_finish_reason == "hitl_pending":
            if not managed_deliveries:
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
                _plain_text(builder.to_dict())
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

        if not managed_deliveries:
            await qs._finalize_streaming_assistant(
                builder=builder,
                bridge=bridge,
                ctx=ctx,
                session_id=session_id,
                user_id=user_id,
                qa_type=qa_type,
                model=model_name,
            )
        plain = _plain_text(builder.to_dict())
        if not plain:
            plain = "（已完成，无文本回复）"
        return ChannelRunResult(
            session_id=session_id,
            assistant_message_id=bridge.assistant_message_id,
            plain_text=plain,
            finish_reason=bridge.last_finish_reason or decision.finish_reason or "stop",
        )
    except BaseException as exc:
        if delivery_worker is not None:
            await delivery_failed(
                "CHANNEL_RUN_CANCELLED"
                if isinstance(exc, asyncio.CancelledError)
                else "CHANNEL_RUN_FAILED"
            )
            await delivery_worker.finalize()
        raise
    finally:
        if managed_deliveries:
            if delivery_worker is not None:
                await run_manager.unregister_delivery(
                    run_id, f"channel:{delivery_id or origin}"
                )


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
    force_enabled_skills: Optional[List[str]] = None,
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

    async with pg_manager.get_async_session_context() as db:
        current_user = await UserService.get_user_by_id(int(user_id), db)
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

        from noesis.services.qa import helpers as qs

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
            request_enabled_skills=force_enabled_skills,
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
            model_id=resolved_model_id,
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

        run_id = str(uuid.uuid4())
        now = int(time.time() * 1000)
        request_identity = f"{origin}:{session_id}:{external_message_id or uuid.uuid4()}"
        client_request_id = f"channel:{hashlib.sha256(request_identity.encode()).hexdigest()[:48]}"
        digest = hashlib.sha256(
            json.dumps(
                {"session_id": session_id, "query": text, "qa_type": qa_type, "origin": origin},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        db.add(
            TAgentRun(
                id=run_id,
                user_id=str(current_user.user_id),
                session_id=session_id,
                assistant_message_id=bridge.assistant_message_id,
                client_request_id=client_request_id,
                request_digest=digest,
                qa_type=qa_type,
                origin=origin,
                status=RunStatus.QUEUED.value,
                last_sequence=0,
                attempt_id=1,
                retry_attempt=0,
                retry_max=0,
                owner_instance_id=f"channel:{origin}",
                snapshot={"parts": []},
                created_at=now,
                updated_at=now,
            )
        )
        delivery_id = str(uuid.uuid4()) if outbound is not None else None
        if delivery_id is not None:
            db.add(
                TAgentDelivery(
                    id=delivery_id,
                    run_id=run_id,
                    delivery_type=channel_type,
                    status="running",
                    attempts=0,
                    created_at=now,
                    updated_at=now,
                )
            )
        await db.execute(
            TChatMessage.__table__.update()
            .where(TChatMessage.id == bridge.assistant_message_id)
            .values(extra={"qa_type": qa_type, "run_id": run_id, "origin": origin})
        )
        await db.commit()

        projection = RunProjection(
            run_id=run_id,
            user_id=str(current_user.user_id),
            session_id=session_id,
            assistant_message_id=bridge.assistant_message_id,
            qa_type=qa_type,
            origin=origin,
        )
        result_future: asyncio.Future[ChannelRunResult] = asyncio.get_running_loop().create_future()

        async def producer(publish) -> None:
            try:
                result = await _headless_stream(
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
                    publish=publish,
                    projection=projection,
                    run_id=run_id,
                    delivery_id=delivery_id,
                )
                if not result_future.done():
                    result_future.set_result(result)
            except BaseException as exc:
                await _set_delivery_result(
                    delivery_id, "error", error_code="CHANNEL_RUN_FAILED"
                )
                await RunService._persist_cancel_or_error(run_id, projection, exc)
                if not result_future.done():
                    if isinstance(exc, asyncio.CancelledError):
                        result_future.cancel()
                    else:
                        result_future.set_exception(exc)

        async def persist_limit(error: RunLimitExceeded) -> None:
            await RunService._persist_cancel_or_error(run_id, projection, error)

        checkpoint_sink = PersistSink(
            checkpoint_interval_seconds=StreamConfig.checkpoint_interval_seconds
        )

        async def persist_checkpoint(request) -> None:
            await RunService._persist_checkpoint(
                request.run_id,
                request.assistant_message_id,
                request.snapshot,
                request.snapshot_sequence,
            )

        handle = await run_manager.start(
            run_id=run_id,
            session_id=session_id,
            user_id=str(current_user.user_id),
            assistant_message_id=bridge.assistant_message_id,
            snapshot_provider=projection.snapshot,
            producer=producer,
            state=projection,
            limit_handler=persist_limit,
            checkpoint_policy=lambda event, _sequence: checkpoint_sink.checkpoint_kind(event),
            checkpoint_handler=persist_checkpoint,
            terminal_handler=RunService._persist_terminal_candidate,
        )
        await db.execute(
            TAgentRun.__table__.update()
            .where(TAgentRun.id == run_id)
            .values(status=RunStatus.RUNNING.value, started_at=now, updated_at=now)
        )
        await db.commit()

        logger.info(
            "channel_run start origin={} session_id={} user_id={} qa_type={}",
            origin,
            session_id,
            current_user.user_id,
            qa_type,
        )
        await handle.producer_task
        return await result_future


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
    from noesis.chat.hitl.pending import pending_hitl
    from noesis.agents.guardrails.session_grants import session_grants
    from noesis.services.hitl_timeout import cancel_hitl_timeout
    from noesis.storage.postgres.models.chat import TChatMessage
    from sqlalchemy import and_, select
    from noesis.services.qa import helpers as qs

    qa_type = IntentEnum.SUPER_AGENT_QA.value[0]

    async with pg_manager.get_async_session_context() as db:
        current_user = await UserService.get_user_by_id(int(user_id), db)
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
        async with pg_manager.get_async_session_context() as persist_db:
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

        for idx, decision in enumerate(decision_payloads):
            action = actions[idx] if idx < len(actions) else {}
            tool_call_id = action.get("tool_call_id")
            name = str(action.get("name") or "")
            dtype = decision.get("type")
            if dtype == "approve":
                builder.update_tool_hitl(
                    tool_call_id,
                    {"status": "approved", "decision": "approve"},
                    status="running",
                    state=ToolState.RUNNING,
                )
            elif dtype == "reject":
                msg_text = decision.get("message") or "用户拒绝了该操作"
                builder.update_tool_hitl(
                    tool_call_id,
                    {"status": "rejected", "decision": "reject"},
                    status="error",
                    state=ToolState.REJECTED,
                )
                try:
                    builder.append_tool_output(
                        name,
                        msg_text,
                        tool_call_id,
                        status="error",
                        error=msg_text,
                        error_category="deterministic",
                        state=ToolState.REJECTED,
                        outcome="rejected",
                    )
                except ValueError:
                    pass
            elif dtype == "respond":
                answer = str(decision.get("message") or "")
                builder.update_tool_hitl(
                    tool_call_id,
                    {"status": "answered", "decision": "respond"},
                    status="success",
                    state=ToolState.SUCCEEDED,
                )
                try:
                    builder.append_tool_output(
                        name,
                        answer,
                        tool_call_id,
                        status="success",
                        state=ToolState.SUCCEEDED,
                        outcome="ok",
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
        active = await AgentRunRepository(db).get_active_for_session(
            str(current_user.user_id), session_id
        )
        if active is None or active.assistant_message_id != aid:
            return ChannelRunResult(
                session_id=session_id,
                assistant_message_id=aid,
                plain_text="本轮任务已经中断，无法继续审批。",
                finish_reason="error",
            )
        try:
            handle = run_manager.get(active.id)
        except KeyError:
            return ChannelRunResult(
                session_id=session_id,
                assistant_message_id=aid,
                plain_text="本轮任务已经中断，无法继续审批。",
                finish_reason="error",
            )
        if not isinstance(handle.state, RunProjection):
            raise RuntimeError("channel run projection unavailable")
        projection = handle.state
        delivery_id = str(uuid.uuid4()) if outbound is not None else None
        if delivery_id is not None:
            now = int(time.time() * 1000)
            db.add(
                TAgentDelivery(
                    id=delivery_id,
                    run_id=active.id,
                    delivery_type=origin,
                    status="running",
                    attempts=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.commit()
        result_future: asyncio.Future[ChannelRunResult] = asyncio.get_running_loop().create_future()

        async def producer(publish) -> None:
            try:
                resumed_result = await _headless_stream(
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
                    publish=publish,
                    projection=projection,
                    run_id=active.id,
                    delivery_id=delivery_id,
                )
                result_future.set_result(resumed_result)
            except BaseException as exc:
                await _set_delivery_result(
                    delivery_id, "error", error_code="CHANNEL_RUN_FAILED"
                )
                await RunService._persist_cancel_or_error(active.id, projection, exc)
                if isinstance(exc, asyncio.CancelledError):
                    result_future.cancel()
                else:
                    result_future.set_exception(exc)

        def prepare_resume() -> None:
            projection.builder = builder
            projection.begin_hitl_resume()

        resumed_handle = await run_manager.resume(
            active.id, producer, prepare=prepare_resume
        )
        await AgentRunRepository(db).compare_and_set_status(
            active.id,
            [RunStatus.HITL_PENDING],
            RunStatus.RUNNING,
            updated_at=int(time.time() * 1000),
        )
        await db.commit()
        await resumed_handle.producer_task
        return await result_future
