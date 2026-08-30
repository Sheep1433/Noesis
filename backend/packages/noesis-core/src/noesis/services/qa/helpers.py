"""Qa helpers: agents, request resolve, persist, stream registry.

实现细节模块；对外入口见 ``services.qa`` / ``QaService``。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.agents.case_generate.case_coordinator import CaseCoordinator
from noesis.agents.common_qa import GeneralQAAgent
from noesis.agents.fault_operation import FaultOperationAgent
from noesis.agents.super_agent import SuperAgent
from noesis.runtime.logging import logger
from noesis.storage.postgres.manager import pg_manager
from noesis.config.env import LangfuseConfig
from noesis.config.mcp_config import (
    MCP_PROFILE_FAULT_OPERATION,
    get_profile_server_names,
)
from noesis.config.code_enum import IntentEnum
from noesis.chat.delivery.events import RunEvent
from noesis.chat.delivery.orchestrator import RunOrchestrator
from noesis.services.persist_sink import PersistSink
from noesis.chat.message_builder import AssistantMessageBuilder
from noesis.chat.event_mapping.failure_notice import (
    append_stream_failure_notice_to_content,
)
from noesis.chat.event_mapping.usage_normalize import USAGE_FIELDS
from noesis.chat.event_mapping.langgraph_bridge import LangGraphSseBridge
from noesis.agents.middlewares.session_stats_registry import SessionStatsRegistry
from noesis.chat.event_mapping.bridge import (
    END_SENTINEL,
    HEARTBEAT_SENTINEL,
    MemoryStreamBridge,
    StreamBridgeError,
    iter_bridge_events,
)
from noesis.chat.event_mapping.mapper import RuntimeEventMapper, new_stream_ctx
from noesis.runtime.deps import langfuse_workflow_context, merge_langfuse_runnable_config
from noesis.llm.catalog import get_default_model_id, resolve_catalog_entry
from noesis.services.chat_service import ChatService


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
    """请求显式携带 model_id 时写入会话；否则读会话 extra；最后回退默认目录项。

    用户自定义模型优先于内置目录：命中时把含解密 key 的快照注入 ContextVar，
    factory/catalog 在本次 run 内据此路由到用户自己的端点。
    """
    from noesis.llm.runtime_snapshot import set_runtime_model_snapshots
    from noesis.services.user_llm_service import UserLLMService

    set_runtime_model_snapshots([])

    async def _apply_custom(model_id: Optional[str]) -> Optional[str]:
        if not model_id:
            return None
        snapshots = await UserLLMService.resolve_runtime_snapshots(
            db, user_id=str(user_id), model_id=model_id
        )
        if not snapshots:
            return None
        set_runtime_model_snapshots(snapshots)
        return snapshots[0].id

    if request_model_id is not None:
        normalized = _normalize_model_id(request_model_id)
        resolved = await _apply_custom(normalized) or resolve_catalog_entry(normalized).id
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
            return await _apply_custom(stored) or resolve_catalog_entry(stored).id
    return get_default_model_id()


def _resolved_model_name(model_id: str) -> str:
    return resolve_catalog_entry(model_id).id

def _assistant_content_snapshot(builder: Optional[AssistantMessageBuilder]) -> Dict[str, Any]:
    if builder and not builder.is_empty():
        return builder.to_dict()
    return {"version": 1, "parts": []}


def _assistant_status_for_finish(finish_reason: str) -> str:
    if finish_reason in {
        "error",
        "context_exhausted",
        "retryable_error",
    }:
        return "error"
    if finish_reason == "hitl_pending":
        return "streaming"
    if finish_reason in {
        "length_stop",
        "safety_stop",
        "partial_output",
        "empty_after_tools",
        "tool_loop_limit",
        "tool_call_limit",
        "subagent_concurrency_limit",
        "subagent_total_limit",
        "subagent_depth_limit",
    }:
        return "partial"
    return "completed"


def _build_assistant_persist_extra(
    *,
    qa_type: str,
    bridge: Optional[LangGraphSseBridge] = None,
    error_message: Optional[str] = None,
    model: Optional[str] = None,
    include_usage: bool = False,
) -> Dict[str, Any]:
    extra: Dict[str, Any] = {"qa_type": qa_type}
    if model:
        extra["model"] = model
    if bridge is not None:
        if bridge.last_finish_reason:
            extra["finish_reason"] = bridge.last_finish_reason
        err = error_message or bridge.last_error_message
        if err:
            extra["error_message"] = err[:8000]
        # 终态写入本条消息的 usage 聚合（主+子 agent 全部模型调用的 token/步数/耗时），
        # 供历史会话打开时回放统计。checkpoint（streaming 态）不写——
        # update_assistant_message 对 usage 键做累加合并，中途写会导致重复计数。
        if include_usage and bridge.message_usage.get("steps"):
            extra["usage"] = dict(bridge.message_usage)
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

    async with pg_manager.get_async_session_context() as persist_db:
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
        from noesis.chat.hitl.pending import PendingHitl, pending_hitl
        from noesis.services.hitl_timeout import schedule_hitl_timeout

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
    extra = _build_assistant_persist_extra(qa_type=qa_type, bridge=bridge, model=model, include_usage=True)
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
    extra: Optional[Dict[str, Any]] = None,
) -> bool:
    """流开始前插入 assistant 骨架行（与 SSE assistant_message_id 同 id）。"""
    try:
        async with pg_manager.get_async_session_context() as persist_db:
            await ChatService.save_message(
                session_id=session_id,
                user_id=user_id,
                role="assistant",
                content={"version": 1, "parts": []},
                extra=extra or {},
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
        async with pg_manager.get_async_session_context() as db:
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


async def _yield_run_events_from_agent(
    agent_generator: AsyncGenerator[Any, None],
    *,
    bridge: LangGraphSseBridge,
    builder: AssistantMessageBuilder,
    ctx: Dict[str, Any],
    session_id: str,
    user_id: str,
    qa_type: str,
    langfuse_thread_id: Optional[str] = None,
    persist_sink: Optional[PersistSink] = None,
) -> AsyncGenerator[RunEvent, None]:
    """目标 Agent Run 的唯一 raw → typed path；不经过 EventBus 或 SSE parser。"""
    sink = persist_sink or PersistSink()
    ctx["_persist_sink"] = sink
    mapper = RuntimeEventMapper(bridge)
    runtime_bridge = MemoryStreamBridge()
    lf_ctx = _langfuse_stream_context(
        session_id, qa_type, thread_id=langfuse_thread_id
    )
    run_id = f"{session_id}:{bridge.assistant_message_id}"
    async for raw in iter_bridge_events(
        runtime_bridge,
        run_id,
        agent_generator,
        keepalive_seconds=0,
        langfuse_context=lf_ctx,
    ):
        if raw is HEARTBEAT_SENTINEL:
            continue
        if raw is END_SENTINEL:
            break
        if isinstance(raw, StreamBridgeError):
            raise raw.exc
        events = mapper.map_item(raw, builder, ctx)
        for event in events:
            sink.on_event(event)
        # 任务模式不经过 SseDelivery 的 on_events 回调；在同一处消费
        # model_end 产生的 context tick，确保实时事件与 session.extra.context
        # 使用同一条持久化路径。函数内部仅在 tick 存在时写库，不会按 token 写库。
        await _persist_stream_checkpoint(bridge, session_id, user_id)
        for event in events:
            yield event


async def _finalize_run_events(
    bridge: LangGraphSseBridge,
    ctx: Dict[str, Any],
    session_id: str,
    user_id: str,
) -> AsyncGenerator[RunEvent, None]:
    mapper = RuntimeEventMapper(bridge)
    finish_reason = "stopped" if ctx.get("user_stopped") else None
    sink = ctx.get("_persist_sink")
    for event in mapper.finalize(finish_reason=finish_reason):
        if isinstance(sink, PersistSink):
            sink.on_event(event)
        yield event


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
        from noesis.chat.delivery.sse import parse_sse_line_to_event

        for line in lines:
            for ev in parse_sse_line_to_event(line):
                sink.on_event(ev)
    for sse_line in lines:
        yield sse_line
        await _persist_stream_checkpoint(bridge, session_id, user_id)




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


async def seed_session_stats_from_history(session_id: str, user_id: str, db: AsyncSession) -> None:
    """进程内首次遇到该会话时，从 DB 历史 assistant 消息 extra.usage 汇总预填 stats registry。

    已累计（本进程一直在跑该会话）则忽略；查询失败静默跳过——seed 只影响
    统计条起点，不值得为它让问答失败。
    """
    if not session_id:
        return
    # 本进程已在累计则直接跳过，避免每个 run 都全量拉取该会话消息做汇总。
    if SessionStatsRegistry.peek(session_id) is not None:
        return
    try:
        from sqlalchemy import select
        from noesis.storage.postgres.models.chat import TChatMessage

        result = await db.execute(
            select(TChatMessage.extra).where(
                TChatMessage.session_id == session_id,
                TChatMessage.user_id == user_id,
                TChatMessage.role == "assistant",
                TChatMessage.deleted_at.is_(None),
            )
        )
        totals: Dict[str, float] = {}
        for (extra,) in result.all():
            usage = extra.get("usage") if isinstance(extra, dict) else None
            if not isinstance(usage, dict):
                continue
            for key in USAGE_FIELDS:
                if key == "turns":
                    continue
                totals[key] = totals.get(key, 0.0) + float(usage.get(key) or 0)
            totals["turns"] = totals.get("turns", 0.0) + 1.0
        if totals:
            SessionStatsRegistry.seed(session_id, totals)
    except Exception:
        logger.debug("seed_session_stats_from_history failed", exc_info=True)
