"""Qa helpers: agents, request resolve, persist, stream registry.

实现细节模块；对外入口见 ``services.qa`` / ``QaService``。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from agent.case_generate.case_coordinator import CaseCoordinator
from agent.profiles.common_react_agent import GeneralQAAgent
from agent.profiles.fault_operation_agent import FaultOperationAgent
from agent.profiles.super_agent import SuperAgent
from common.logging import logger
from config.database import AsyncSessionLocal
from config.env import LangfuseConfig
from config.mcp_config import (
    MCP_PROFILE_FAULT_OPERATION,
    get_profile_server_names,
)
from constants.code_enum import IntentEnum
from domain.chat.delivery.events import RunEvent
from domain.chat.delivery.orchestrator import RunOrchestrator
from domain.chat.delivery.persist_sink import PersistSink
from domain.chat.message_builder import AssistantMessageBuilder
from domain.chat.streaming.failure_notice import (
    append_disconnect_partial_content,
    append_stream_failure_notice_to_content,
)
from domain.chat.streaming.langgraph_sse import LangGraphSseBridge
from domain.observability.langfuse import langfuse_workflow_context, merge_langfuse_runnable_config
from llm.catalog import get_default_model_id, resolve_catalog_entry
from services.chat_service import ChatService


common_agent = GeneralQAAgent()
fault_agent = FaultOperationAgent()
super_agent = SuperAgent()
case_coordinator = CaseCoordinator()


def _normalize_kb_collections(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: List[str] = []
    for item in raw:
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _normalize_id_list(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: List[str] = []
    for item in raw:
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


async def _resolve_mcp_servers_for_query(
    *,
    session_id: str,
    user_id: str,
    qa_type: str,
    request_mcp_servers: Optional[List[str]],
    db: AsyncSession,
) -> List[str]:
    if request_mcp_servers is not None:
        normalized = _normalize_id_list(request_mcp_servers)
        await ChatService.merge_session_extra(
            session_id,
            user_id,
            {"mcp_servers": normalized},
            db=db,
        )
        return normalized

    session = await ChatService.get_session_by_id(
        session_id,
        user_id=user_id,
        db=db,
    )
    extra = session.extra if session and session.extra else {}
    if "mcp_servers" in extra:
        return _normalize_id_list(extra.get("mcp_servers"))

    if qa_type == IntentEnum.FAULT_OPERATION_QA.value[0]:
        try:
            return get_profile_server_names(MCP_PROFILE_FAULT_OPERATION)
        except KeyError:
            logger.warning("FAULT_OPERATION 缺省 profile 不存在，mcp_servers=[]")
            return []
    return []


async def _resolve_enabled_skills_for_query(
    *,
    session_id: str,
    user_id: str,
    request_enabled_skills: Optional[List[str]],
    db: AsyncSession,
) -> Optional[List[str]]:
    if request_enabled_skills is not None:
        normalized = _normalize_id_list(request_enabled_skills)
        await ChatService.merge_session_extra(
            session_id,
            user_id,
            {"enabled_skills": normalized},
            db=db,
        )
        return normalized

    session = await ChatService.get_session_by_id(
        session_id,
        user_id=user_id,
        db=db,
    )
    extra = session.extra if session and session.extra else {}
    if "enabled_skills" not in extra:
        return None
    return _normalize_id_list(extra.get("enabled_skills"))


async def _resolve_kb_settings_for_query(
    *,
    session_id: str,
    user_id: str,
    request_kb_collections: Optional[List[str]],
    request_kb_search_enabled: Optional[bool],
    db: AsyncSession,
) -> Tuple[List[str], bool]:
    """解析并持久化会话知识库范围与启用状态。"""
    if request_kb_collections is not None or request_kb_search_enabled is not None:
        session = await ChatService.get_session_by_id(
            session_id,
            user_id=user_id,
            db=db,
        )
        stored_extra = session.extra if session and session.extra else {}
        normalized = (
            _normalize_kb_collections(request_kb_collections)
            if request_kb_collections is not None
            else _normalize_kb_collections(stored_extra.get("kb_collections"))
        )
        enabled = (
            request_kb_search_enabled
            if request_kb_search_enabled is not None
            else stored_extra.get("kb_search_enabled") is not False
        )
        await ChatService.merge_session_extra(
            session_id,
            user_id,
            {"kb_collections": normalized, "kb_search_enabled": enabled},
            db=db,
        )
        return normalized, enabled

    session = await ChatService.get_session_by_id(
        session_id,
        user_id=user_id,
        db=db,
    )
    if not session or not session.extra:
        return [], True
    return (
        _normalize_kb_collections(session.extra.get("kb_collections")),
        session.extra.get("kb_search_enabled") is not False,
    )


def _normalize_model_id(raw: Any) -> Optional[str]:
    model_id = str(raw or "").strip()
    return model_id or None


async def _resolve_model_for_query(
    *,
    session_id: str,
    user_id: str,
    request_model_id: Optional[str],
    db: AsyncSession,
) -> str:
    """请求显式携带 model_id 时写入会话；否则读会话 extra；最后回退默认目录项。"""
    if request_model_id is not None:
        normalized = _normalize_model_id(request_model_id)
        resolved = resolve_catalog_entry(normalized).id
        await ChatService.merge_session_extra(
            session_id,
            user_id,
            {"model_id": resolved},
            db=db,
        )
        return resolved

    session = await ChatService.get_session_by_id(
        session_id,
        user_id=user_id,
        db=db,
    )
    if session and session.extra:
        stored = _normalize_model_id(session.extra.get("model_id"))
        if stored:
            return resolve_catalog_entry(stored).id
    return get_default_model_id()


def _resolved_model_name(model_id: str) -> str:
    return resolve_catalog_entry(model_id).model_name

def _assistant_content_snapshot(builder: Optional[AssistantMessageBuilder]) -> Dict[str, Any]:
    if builder and not builder.is_empty():
        return builder.to_dict()
    return {"version": 1, "parts": []}


def _assistant_status_for_finish(finish_reason: str) -> str:
    if finish_reason == "error":
        return "error"
    if finish_reason == "hitl_pending":
        return "streaming"
    return "completed"


def _build_assistant_persist_extra(
    *,
    qa_type: str,
    bridge: Optional[LangGraphSseBridge] = None,
    error_message: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    extra: Dict[str, Any] = {"qa_type": qa_type}
    if model:
        extra["model"] = model
    if bridge is not None:
        if bridge.last_finish_usage:
            extra["usage"] = bridge.last_finish_usage
        if bridge.last_finish_reason:
            extra["finish_reason"] = bridge.last_finish_reason
        err = error_message or bridge.last_error_message
        if err:
            extra["error_message"] = err[:8000]
    elif error_message:
        extra["error_message"] = error_message[:8000]
    return extra


def _assistant_content_for_persist(
    builder: Optional[AssistantMessageBuilder],
    *,
    error_detail: str = "",
) -> Dict[str, Any]:
    content = _assistant_content_snapshot(builder)
    if error_detail:
        content = append_stream_failure_notice_to_content(content, error_detail)
    return content


def _resolve_assistant_message_id(
    ctx: Dict[str, Any],
    builder: Optional[AssistantMessageBuilder],
) -> Optional[str]:
    """流式落库主键：优先 ctx 骨架 id，其次 builder.message_id（与 SSE assistant_message_id 对齐）。"""
    aid = ctx.get("_assistant_db_id")
    if aid:
        return str(aid)
    if builder is not None:
        mid = getattr(builder, "message_id", None)
        if mid:
            return str(mid)
    return None


def _stream_terminal_persist_done(ctx: Dict[str, Any]) -> bool:
    return bool(ctx.get("user_stopped") or ctx.get("_stream_persist_finalized"))


def _mark_stream_persist_finalized(ctx: Dict[str, Any]) -> None:
    ctx["_stream_persist_finalized"] = True


async def _persist_assistant(
    content_dict: Dict[str, Any],
    session_id: str,
    user_id: str,
    *,
    status: str = "completed",
    extra: Optional[Dict[str, Any]] = None,
    assistant_message_id: Optional[str] = None,
) -> None:
    """写入或更新 assistant 消息：若提供 assistant_message_id 则先 UPDATE（W2），否则 INSERT（兼容无骨架）。"""
    msg_extra = dict(extra) if extra else {}
    has_parts = bool(content_dict and content_dict.get("parts"))
    content_for_db = content_dict if has_parts else {"version": 1, "parts": []}

    if not assistant_message_id and not has_parts:
        return

    async with AsyncSessionLocal() as persist_db:
        if assistant_message_id:
            ok = await ChatService.update_assistant_message(
                message_id=assistant_message_id,
                session_id=session_id,
                user_id=user_id,
                content=content_for_db,
                status=status,
                extra=msg_extra,
                db=persist_db,
            )
            if not ok:
                await ChatService.save_message(
                    session_id=session_id,
                    user_id=user_id,
                    role="assistant",
                    content=content_for_db,
                    extra=msg_extra,
                    status=status,
                    message_id=assistant_message_id,
                    db=persist_db,
                )
        else:
            if not has_parts:
                return
            await ChatService.save_message(
                session_id=session_id,
                user_id=user_id,
                role="assistant",
                content=content_for_db,
                extra=msg_extra,
                status=status,
                db=persist_db,
            )


async def _persist_hitl_pending_assistant(
    *,
    builder: Optional[AssistantMessageBuilder],
    bridge: LangGraphSseBridge,
    ctx: Dict[str, Any],
    session_id: str,
    user_id: str,
    qa_type: str,
    model: Optional[str] = None,
) -> None:
    """HITL 等待：UPDATE content + status=streaming，不标记终态 finalized。"""
    if ctx.get("user_stopped"):
        return
    if ctx.get("text_buffer") and builder:
        builder.append_text_delta(
            ctx["text_buffer"],
            parent_task_call_id=ctx.get("text_buffer_parent_task_call_id"),
        )
        ctx["text_buffer"] = ""
        ctx["text_buffer_parent_task_call_id"] = None
    content = _assistant_content_snapshot(builder)
    extra = _build_assistant_persist_extra(qa_type=qa_type, bridge=bridge, model=model)
    aid = _resolve_assistant_message_id(ctx, builder)
    if not aid:
        return
    await _persist_assistant(
        content,
        session_id,
        user_id,
        status="streaming",
        extra=extra,
        assistant_message_id=aid,
    )
    hitl = bridge.last_hitl_payload or {}
    if hitl.get("interrupt_id"):
        from domain.chat.hitl.pending import PendingHitl, pending_hitl
        from domain.chat.hitl.timeout import schedule_hitl_timeout

        pending = PendingHitl(
            interrupt_id=str(hitl["interrupt_id"]),
            session_id=session_id,
            user_id=str(user_id),
            assistant_message_id=str(aid),
            expires_at=float(hitl.get("expires_at") or 0),
            kind=str(hitl.get("kind") or "approval"),
            action_requests=list(hitl.get("action_requests") or []),
            review_configs=list(hitl.get("review_configs") or []),
        )
        pending_hitl.put(pending)
        schedule_hitl_timeout(pending)


async def _finalize_streaming_assistant(
    *,
    builder: Optional[AssistantMessageBuilder],
    bridge: LangGraphSseBridge,
    ctx: Dict[str, Any],
    session_id: str,
    user_id: str,
    qa_type: str,
    model: Optional[str] = None,
) -> None:
    if _stream_terminal_persist_done(ctx):
        return
    if ctx.get("text_buffer") and builder:
        builder.append_text_delta(
            ctx["text_buffer"],
            parent_task_call_id=ctx.get("text_buffer_parent_task_call_id"),
        )
        ctx["text_buffer"] = ""
        ctx["text_buffer_parent_task_call_id"] = None
    fin_reason = bridge.last_finish_reason or "stop"
    status = _assistant_status_for_finish(fin_reason)
    error_detail = bridge.last_error_message if fin_reason == "error" else ""
    content = _assistant_content_for_persist(builder, error_detail=error_detail)
    extra = _build_assistant_persist_extra(qa_type=qa_type, bridge=bridge, model=model)
    aid = _resolve_assistant_message_id(ctx, builder)
    if not aid and (not builder or builder.is_empty()):
        return
    await _persist_assistant(
        content,
        session_id,
        user_id,
        status=status,
        extra=extra,
        assistant_message_id=aid,
    )
    _mark_stream_persist_finalized(ctx)


async def _insert_streaming_assistant_skeleton(
    assistant_message_id: str,
    session_id: str,
    user_id: str,
) -> bool:
    """流开始前插入 assistant 骨架行（与 SSE assistant_message_id 同 id）。"""
    try:
        async with AsyncSessionLocal() as persist_db:
            await ChatService.save_message(
                session_id=session_id,
                user_id=user_id,
                role="assistant",
                content={"version": 1, "parts": []},
                extra={},
                status="streaming",
                message_id=assistant_message_id,
                db=persist_db,
            )
        return True
    except Exception:
        logger.exception(
            f"assistant streaming 骨架行插入失败 session_id={session_id} message_id={assistant_message_id}"
        )
        return False


async def _persist_session_context_snapshot(
    bridge: LangGraphSseBridge,
    session_id: str,
    user_id: str,
) -> None:
    snapshot = bridge.last_context_snapshot
    if not snapshot or not snapshot.get("max_tokens"):
        return
    payload = {
        **snapshot,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        async with AsyncSessionLocal() as db:
            await ChatService.merge_session_extra(
                session_id,
                user_id,
                {"context": payload},
                db=db,
            )
    except Exception:
        logger.exception(f"会话 context 快照落库失败 session_id={session_id}")


async def _persist_stream_checkpoint(
    bridge: LangGraphSseBridge,
    session_id: str,
    user_id: str,
) -> None:
    """流式过程中仅落库会话 context 快照；assistant 正文在终态/断连时一次性写入。"""
    if bridge.consume_session_context_tick():
        await _persist_session_context_snapshot(bridge, session_id, user_id)
    # persist_tick（part 边界）故意丢弃，避免中间态 assistant 落库

_run_orchestrator = RunOrchestrator()

# Shared with QaService._active_streams (same dict object)
ACTIVE_STREAMS: Dict[str, "_ActiveStreamState"] = {}


def _langfuse_stream_context(
    session_id: str,
    qa_type: str,
    *,
    thread_id: Optional[str] = None,
):
    """为生产者 Task 构建 Langfuse workflow context（未启用时返回 None）。"""
    if not LangfuseConfig.langfuse_tracing_enabled:
        return None
    tid = thread_id or session_id
    lf_config = merge_langfuse_runnable_config(
        {"configurable": {"thread_id": tid}},
        langfuse_session_id=session_id,
        qa_type=qa_type,
        enabled=True,
        langfuse_trace_id=session_id,
    )
    return langfuse_workflow_context(lf_config)


def _new_stream_ctx() -> Dict[str, Any]:
    return {
        "text_buffer": "",
        "current_tool_name": None,
        "current_tool_call_id": None,
        "tool_start_times": {},
        "usage_cumulative": {"input_tokens": 0, "output_tokens": 0},
        "usage_seen_run_ids": set(),
        "_assistant_db_id": None,
    }


async def _yield_sse_from_agent_bridge(
    agent_generator: AsyncGenerator[Any, None],
    *,
    bridge: LangGraphSseBridge,
    builder: AssistantMessageBuilder,
    ctx: Dict[str, Any],
    session_id: str,
    user_id: str,
    qa_type: str,
    keepalive_seconds: float,
    langfuse_thread_id: Optional[str] = None,
    persist_sink: Optional[PersistSink] = None,
) -> AsyncGenerator[str, None]:
    """经 RunOrchestrator Fan-out：RunEvent 总线 + SseDelivery（keepalive 仅在投递层）。"""
    sink = persist_sink or PersistSink()
    ctx["_persist_sink"] = sink
    lf_ctx = _langfuse_stream_context(
        session_id, qa_type, thread_id=langfuse_thread_id
    )

    async def on_events(events: List[RunEvent]) -> None:
        for ev in events:
            sink.on_event(ev)
        await _persist_stream_checkpoint(bridge, session_id, user_id)

    async for sse_line in _run_orchestrator.stream_sse(
        agent_generator,
        bridge=bridge,
        builder=builder,
        ctx=ctx,
        session_id=session_id,
        keepalive_seconds=keepalive_seconds,
        origin="web",
        langfuse_context=lf_ctx,
        on_events=on_events,
    ):
        yield sse_line


async def _finalize_sse_bridge_stream(
    bridge: LangGraphSseBridge,
    builder: AssistantMessageBuilder,
    ctx: Dict[str, Any],
    session_id: str,
    user_id: str,
) -> AsyncGenerator[str, None]:
    finish_reason = "stopped" if ctx.get("user_stopped") else None
    lines = _run_orchestrator.finalize_sse(bridge, finish_reason=finish_reason)
    sink = ctx.get("_persist_sink")
    if isinstance(sink, PersistSink):
        from domain.chat.delivery.sse import parse_sse_line_to_event

        for line in lines:
            for ev in parse_sse_line_to_event(line):
                sink.on_event(ev)
    for sse_line in lines:
        yield sse_line
        await _persist_stream_checkpoint(bridge, session_id, user_id)


@dataclass
class _ActiveStreamState:
    builder: AssistantMessageBuilder
    ctx: Dict[str, Any]
    qa_type: str
    model_name: str = ""
    user_stopped: bool = False


def _register_active_stream(session_id: str, state: _ActiveStreamState) -> None:
    from domain.chat.delivery.orchestrator import run_lifecycle

    ACTIVE_STREAMS[session_id] = state
    run_lifecycle.register(
        session_id,
        {
            "ctx": state.ctx,
            "builder": state.builder,
            "qa_type": state.qa_type,
        },
    )


def _unregister_active_stream(session_id: str) -> None:
    from domain.chat.delivery.orchestrator import run_lifecycle

    ACTIVE_STREAMS.pop(session_id, None)
    run_lifecycle.pop(session_id)


def _flush_ctx_text_buffer(
    ctx: Dict[str, Any],
    builder: Optional[AssistantMessageBuilder],
) -> None:
    buf = ctx.get("text_buffer") or ""
    if not buf or builder is None:
        return
    parent = ctx.get("text_buffer_parent_task_call_id")
    builder.append_text_delta(buf, parent_task_call_id=parent)
    ctx["text_buffer"] = ""
    ctx["text_buffer_parent_task_call_id"] = None


async def _persist_disconnect_partial(
    *,
    builder: Optional[AssistantMessageBuilder],
    ctx: Dict[str, Any],
    session_id: str,
    user_id: str,
    qa_type: str,
    assistant_message_id: Optional[str],
) -> None:
    """连接意外断开：partial 落库，无用户中断文案。"""
    if _stream_terminal_persist_done(ctx):
        return
    _flush_ctx_text_buffer(ctx, builder)
    if builder and not builder.is_empty():
        content = append_disconnect_partial_content(builder.to_dict())
        try:
            await _persist_assistant(
                content,
                session_id,
                user_id,
                status="partial",
                extra={"qa_type": qa_type},
                assistant_message_id=assistant_message_id,
            )
        except Exception:
            logger.exception(
                f"连接中断 assistant 消息落库失败: session_id={session_id} user_id={user_id}"
            )
    elif assistant_message_id:
        try:
            await _persist_assistant(
                {"version": 1, "parts": []},
                session_id,
                user_id,
                status="partial",
                extra={"qa_type": qa_type},
                assistant_message_id=assistant_message_id,
            )
        except Exception:
            logger.exception(
                f"连接中断 assistant 空内容落库失败: session_id={session_id} user_id={user_id}"
            )
    _mark_stream_persist_finalized(ctx)


async def _handle_stream_client_disconnect(
    *,
    session_id: str,
    qa_type: str,
    user_id: str,
    ctx: Dict[str, Any],
    builder: Optional[AssistantMessageBuilder],
    log_label: str,
) -> None:
    """客户端断开连接：将已生成内容 partial 落库（shield 避免 CancelledError 打断 commit）。"""
    from domain.chat.delivery.orchestrator import CancelReason, run_lifecycle

    stream_ctx = ctx or {}
    if _stream_terminal_persist_done(stream_ctx):
        return
    run_lifecycle.notify_cancel(session_id, CancelReason.DISCONNECT)
    stream_state = ACTIVE_STREAMS.get(session_id)
    persist_b = stream_state.builder if stream_state else builder
    aid = _resolve_assistant_message_id(stream_ctx, persist_b)
    try:
        await asyncio.shield(
            _persist_disconnect_partial(
                builder=persist_b,
                ctx=stream_ctx,
                session_id=session_id,
                user_id=user_id,
                qa_type=qa_type,
                assistant_message_id=aid,
            )
        )
    except asyncio.CancelledError:
        if _stream_terminal_persist_done(stream_ctx):
            raise
        logger.warning(
            f"{log_label} 断开落库被取消，尝试 detached 落库 session_id={session_id} "
            f"assistant_db_id={aid}"
        )
        asyncio.create_task(
            _persist_disconnect_partial(
                builder=persist_b,
                ctx=stream_ctx,
                session_id=session_id,
                user_id=user_id,
                qa_type=qa_type,
                assistant_message_id=aid,
            )
        )
        raise
