"""
QaService - 问答服务

流式输出为 Noesis SSE 文本帧（str）；上游为 LangGraph astream_events + 少量控制 dict，由 LangGraphSseBridge 转换。
"""
import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from agent.common_react_agent import GeneralQAAgent
from agent.fault_operation_agent import FaultOperationAgent
from agent.deep_research_agent import DeepResearchAgent
from agent.case_generate.case_coordinator import CaseCoordinator

from config.database import AsyncSessionLocal
from config.env import LangfuseConfig, StreamConfig
from constants.code_enum import IntentEnum
from schemas.login_vo import CurrentUser
from schemas.qa_vo import QaQueryRequest
from services.chat_service import ChatService
from domain.observability.langfuse import langfuse_workflow_context, merge_langfuse_runnable_config
from domain.chat.streaming.langgraph_sse import LangGraphSseBridge, bridge_raw_to_sse_lines
from common.logging import logger
from domain.chat.message_builder import AssistantMessageBuilder, UserMessageBuilder
from domain.chat.streaming.bridge import MemoryStreamBridge, iter_bridge_events
from domain.auth.stop_token import StopTokenService
from domain.chat.streaming.failure_notice import (
    append_disconnect_partial_content,
    append_stream_failure_notice_to_content,
    append_user_stop_notice_to_content,
)


common_agent = GeneralQAAgent()
fault_agent = FaultOperationAgent()
deep_research_agent = DeepResearchAgent()
case_coordinator = CaseCoordinator()

SSE_COMMENT_KEEPALIVE = ": keepalive\n\n"


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


def _bridge_run_id(session_id: str, assistant_message_id: str) -> str:
    return f"{session_id}:{assistant_message_id}"


async def _iter_agent_stream_via_bridge(
    agent_generator: AsyncGenerator[Any, None],
    *,
    session_id: str,
    assistant_message_id: str,
    qa_type: str,
    keepalive_seconds: float,
    langfuse_thread_id: Optional[str] = None,
) -> AsyncGenerator[Any, None]:
    """生产者 Task 发布事件，SSE 侧订阅；心跳来自订阅空闲超时。"""
    bridge = MemoryStreamBridge()
    run_id = _bridge_run_id(session_id, assistant_message_id)
    lf_ctx = _langfuse_stream_context(
        session_id, qa_type, thread_id=langfuse_thread_id
    )
    async for event in iter_bridge_events(
        bridge,
        run_id,
        agent_generator,
        keepalive_seconds=keepalive_seconds,
        langfuse_context=lf_ctx,
    ):
        yield event


def _assistant_content_snapshot(builder: Optional[AssistantMessageBuilder]) -> Dict[str, Any]:
    if builder and not builder.is_empty():
        return builder.to_dict()
    return {"version": 1, "parts": []}


def _assistant_status_for_finish(finish_reason: str) -> str:
    return "error" if finish_reason == "error" else "completed"


def _build_assistant_persist_extra(
    *,
    qa_type: str,
    bridge: Optional[LangGraphSseBridge] = None,
    error_message: Optional[str] = None,
) -> Dict[str, Any]:
    extra: Dict[str, Any] = {"qa_type": qa_type}
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


async def _finalize_streaming_assistant(
    *,
    builder: Optional[AssistantMessageBuilder],
    bridge: LangGraphSseBridge,
    ctx: Dict[str, Any],
    session_id: str,
    user_id: str,
    qa_type: str,
) -> None:
    if ctx.get("text_buffer") and builder:
        builder.append_text(ctx["text_buffer"])
        ctx["text_buffer"] = ""
    fin_reason = bridge.last_finish_reason or "stop"
    status = _assistant_status_for_finish(fin_reason)
    error_detail = bridge.last_error_message if fin_reason == "error" else ""
    content = _assistant_content_for_persist(builder, error_detail=error_detail)
    extra = _build_assistant_persist_extra(qa_type=qa_type, bridge=bridge)
    aid = ctx.get("_assistant_db_id")
    if aid:
        await _persist_assistant(
            content,
            session_id,
            user_id,
            status=status,
            extra=extra,
            assistant_message_id=aid,
        )
    elif builder and not builder.is_empty():
        await _persist_assistant(
            content,
            session_id,
            user_id,
            status=status,
            extra=extra,
        )


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


async def _persist_stream_checkpoint(
    bridge: LangGraphSseBridge,
    ctx: Dict[str, Any],
    builder: AssistantMessageBuilder,
    session_id: str,
    user_id: str,
) -> None:
    _flush_ctx_text_buffer(ctx, builder)
    if bridge.consume_session_context_tick():
        await _persist_session_context_snapshot(bridge, session_id, user_id)
    if not bridge.consume_persist_tick() or not ctx.get("_assistant_db_id"):
        return
    try:
        await _persist_assistant(
            _assistant_content_snapshot(builder),
            session_id,
            user_id,
            status="streaming",
            assistant_message_id=ctx["_assistant_db_id"],
        )
    except Exception:
        logger.exception(f"assistant 流式检查点落库失败 session_id={session_id}")


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
) -> AsyncGenerator[str, None]:
    async for raw in _iter_agent_stream_via_bridge(
        agent_generator,
        session_id=session_id,
        assistant_message_id=bridge.assistant_message_id,
        qa_type=qa_type,
        keepalive_seconds=keepalive_seconds,
        langfuse_thread_id=langfuse_thread_id,
    ):
        lines = bridge_raw_to_sse_lines(
            raw,
            bridge,
            builder,
            ctx,
            keepalive_comment=SSE_COMMENT_KEEPALIVE,
        )
        if lines is None:
            continue
        for sse_line in lines:
            yield sse_line
        await _persist_stream_checkpoint(bridge, ctx, builder, session_id, user_id)


async def _finalize_sse_bridge_stream(
    bridge: LangGraphSseBridge,
    builder: AssistantMessageBuilder,
    ctx: Dict[str, Any],
    session_id: str,
    user_id: str,
) -> AsyncGenerator[str, None]:
    _flush_ctx_text_buffer(ctx, builder)
    finish_reason = "stopped" if ctx.get("user_stopped") else None
    for sse_line in bridge.finalize(finish_reason=finish_reason):
        yield sse_line
        await _persist_stream_checkpoint(bridge, ctx, builder, session_id, user_id)


@dataclass
class _ActiveStreamState:
    builder: AssistantMessageBuilder
    ctx: Dict[str, Any]
    qa_type: str
    user_stopped: bool = False


def _flush_ctx_text_buffer(
    ctx: Dict[str, Any],
    builder: Optional[AssistantMessageBuilder],
) -> None:
    buf = ctx.get("text_buffer") or ""
    if not buf or builder is None:
        return
    parent = ctx.get("text_buffer_parent_task_call_id")
    builder.append_text(buf, parent_task_call_id=parent)
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
    if (ctx or {}).get("user_stopped"):
        return
    aid = (ctx or {}).get("_assistant_db_id")
    stream_state = QaService._active_streams.get(session_id)
    persist_b = stream_state.builder if stream_state else builder
    try:
        await asyncio.shield(
            _persist_disconnect_partial(
                builder=persist_b,
                ctx=ctx or {},
                session_id=session_id,
                user_id=user_id,
                qa_type=qa_type,
                assistant_message_id=aid,
            )
        )
    except asyncio.CancelledError:
        logger.warning(
            f"{log_label} 断开落库被取消，尝试 detached 落库 session_id={session_id} "
            f"assistant_db_id={aid}"
        )
        asyncio.create_task(
            _persist_disconnect_partial(
                builder=persist_b,
                ctx=ctx or {},
                session_id=session_id,
                user_id=user_id,
                qa_type=qa_type,
                assistant_message_id=aid,
            )
        )
        raise


class QaService:
    # session_id -> 流式状态（stop 时权威落库）
    _active_streams: Dict[str, _ActiveStreamState] = {}

    @classmethod
    async def exec_query(
        cls,
        req_obj: QaQueryRequest,
        current_user: CurrentUser,
        db: AsyncSession
    ) -> AsyncGenerator[str, None]:
        """
        执行问答，返回 Noesis 标准 SSE 文本帧（str）。

        Yields:
            str: SSE 帧（含换行），末尾由 bridge.finalize() 追加 [DONE]
        """
        logging.info(f"query param: {req_obj.json()}")
        clean_query = re.sub(r"\s+", "", req_obj.query or "")

        session_id = req_obj.chat_id or str(uuid.uuid4())
        task_cancelled = False
        user_text = (req_obj.query or "").strip()
        builder: Optional[AssistantMessageBuilder] = None
        bridge: Optional[LangGraphSseBridge] = None
        ctx: Dict[str, Any] = {}

        try:
            # 创建或获取会话
            await ChatService.get_or_create_session(
                user_id=current_user.user_id,
                session_id=session_id,
                title=(user_text[:100] if user_text else (clean_query[:100] if clean_query else None)),
                extra={"qa_type": req_obj.qa_type},
                db=db
            )

            # 流式开始前写入用户消息
            if user_text:
                await ChatService.save_message(
                    session_id=session_id,
                    user_id=current_user.user_id,
                    role="user",
                    content=UserMessageBuilder(content=user_text).serialize(),
                    extra={
                        "qa_type": req_obj.qa_type,
                        "file_dict": req_obj.file_dict,
                    },
                    db=db,
                )

            logger.info(
                f"exec_query 流式上游开始 session_id={session_id} qa_type={req_obj.qa_type} user_id={current_user.user_id}"
            )

            # 根据 qa_type 选择 agent 并执行
            if req_obj.qa_type == IntentEnum.COMMON_QA.value[0]:
                agent_generator = common_agent.run_agent(
                    clean_query,
                    session_id=session_id,
                    current_user=current_user,
                    file_list=req_obj.file_dict,
                    qa_type=req_obj.qa_type,
                    db=db,
                )
            elif req_obj.qa_type == IntentEnum.FAULT_OPERATION_QA.value[0]:
                agent_generator = fault_agent.run_agent(
                    clean_query,
                    session_id=session_id,
                    current_user=current_user,
                    file_list=req_obj.file_dict,
                    qa_type=req_obj.qa_type,
                )
            elif req_obj.qa_type == IntentEnum.TEST_CASE_QA.value[0]:
                agent_generator = case_coordinator.run_agent(
                    clean_query,
                    session_id,
                    req_obj.file_dict,
                    qa_type=req_obj.qa_type,
                )
            elif req_obj.qa_type == IntentEnum.DEEP_RESEARCH_QA.value[0]:
                agent_generator = deep_research_agent.run_agent(
                    clean_query,
                    session_id=session_id,
                    current_user=current_user,
                    file_list=req_obj.file_dict,
                    qa_type=req_obj.qa_type,
                )
            else:
                # 即时代码路径，连续产出多帧 SSE，无长时间阻塞，无需注释保活。
                br = LangGraphSseBridge(
                    session_id,
                    emit_langfuse_session_hint=LangfuseConfig.langfuse_tracing_enabled,
                )
                ctx_err: Dict[str, Any] = {}
                for line in br.process_item({"type": "error", "content": "未知的qa_type"}, None, ctx_err):
                    yield line
                for line in br.process_item({"type": "finish", "finish_reason": "error", "usage": {}}, None, ctx_err):
                    yield line
                for line in br.finalize():
                    yield line
                return

            bridge = LangGraphSseBridge(
                session_id,
                emit_langfuse_session_hint=LangfuseConfig.langfuse_tracing_enabled,
                stop_token=StopTokenService.create(session_id, int(current_user.user_id)),
            )
            builder = AssistantMessageBuilder(
                session_id=session_id,
                message_id=bridge.assistant_message_id,
            )
            ctx = _new_stream_ctx()
            cls._active_streams[session_id] = _ActiveStreamState(
                builder=builder,
                ctx=ctx,
                qa_type=req_obj.qa_type,
            )
            if await _insert_streaming_assistant_skeleton(
                bridge.assistant_message_id, session_id, current_user.user_id
            ):
                ctx["_assistant_db_id"] = bridge.assistant_message_id

            ka_sec = float(StreamConfig.sse_keepalive_interval_seconds)
            lf_thread = (
                f"case_graph_{session_id}"
                if req_obj.qa_type == IntentEnum.TEST_CASE_QA.value[0]
                else None
            )
            async for sse_line in _yield_sse_from_agent_bridge(
                agent_generator,
                bridge=bridge,
                builder=builder,
                ctx=ctx,
                session_id=session_id,
                user_id=current_user.user_id,
                qa_type=req_obj.qa_type,
                keepalive_seconds=ka_sec,
                langfuse_thread_id=lf_thread,
            ):
                yield sse_line

            async for sse_line in _finalize_sse_bridge_stream(
                bridge, builder, ctx, session_id, current_user.user_id
            ):
                yield sse_line

            if not task_cancelled and not ctx.get("user_stopped"):
                await _finalize_streaming_assistant(
                    builder=builder,
                    bridge=bridge,
                    ctx=ctx,
                    session_id=session_id,
                    user_id=current_user.user_id,
                    qa_type=req_obj.qa_type,
                )

            logger.info(
                f"exec_query 流式正常结束 session_id={session_id} qa_type={req_obj.qa_type} "
                f"assistant_message_id={bridge.assistant_message_id if bridge else ''} "
                f"finish_reason={bridge.last_finish_reason if bridge else ''}"
            )

            cls._active_streams.pop(session_id, None)

        except asyncio.CancelledError:
            task_cancelled = True
            logger.info(
                f"exec_query 流式任务被取消(CancelledError) session_id={session_id} qa_type={req_obj.qa_type} "
                f"user_id={current_user.user_id} assistant_db_id={(ctx or {}).get('_assistant_db_id')} "
                f"user_stopped={bool((ctx or {}).get('user_stopped'))}"
            )
            await _handle_stream_client_disconnect(
                session_id=session_id,
                qa_type=req_obj.qa_type,
                user_id=current_user.user_id,
                ctx=ctx or {},
                builder=builder,
                log_label="exec_query",
            )
            cls._active_streams.pop(session_id, None)
            raise

        except GeneratorExit:
            task_cancelled = True
            logger.info(
                f"exec_query 流式消费者断开(GeneratorExit) session_id={session_id} qa_type={req_obj.qa_type} "
                f"user_id={current_user.user_id} assistant_db_id={(ctx or {}).get('_assistant_db_id')}"
            )
            await _handle_stream_client_disconnect(
                session_id=session_id,
                qa_type=req_obj.qa_type,
                user_id=current_user.user_id,
                ctx=ctx or {},
                builder=builder,
                log_label="exec_query",
            )
            cls._active_streams.pop(session_id, None)
            raise

        except Exception as e:
            logging.exception(f"QA服务异常: {e}")
            aid = (ctx or {}).get("_assistant_db_id")
            stream_state = cls._active_streams.get(session_id)
            persist_b = stream_state.builder if stream_state else builder
            _flush_ctx_text_buffer(ctx, persist_b)
            err_text = str(e)[:8000]
            if persist_b is not None and not persist_b.is_empty():
                try:
                    await _persist_assistant(
                        _assistant_content_for_persist(persist_b, error_detail=err_text),
                        session_id,
                        current_user.user_id,
                        status="error",
                        extra=_build_assistant_persist_extra(
                            qa_type=req_obj.qa_type,
                            error_message=err_text,
                        ),
                        assistant_message_id=aid,
                    )
                except Exception:
                    logger.exception(
                        f"QA 异常路径 assistant 落库失败: session_id={session_id} user_id={current_user.user_id}"
                    )
            elif aid:
                try:
                    await _persist_assistant(
                        _assistant_content_for_persist(None, error_detail=err_text),
                        session_id,
                        current_user.user_id,
                        status="error",
                        extra=_build_assistant_persist_extra(
                            qa_type=req_obj.qa_type,
                            error_message=err_text,
                        ),
                        assistant_message_id=aid,
                    )
                except Exception:
                    logger.exception(
                        f"QA 异常路径 assistant 空内容落库失败: session_id={session_id} user_id={current_user.user_id}"
                    )
            if bridge is not None:
                b = persist_b if persist_b is not None else AssistantMessageBuilder(session_id=session_id)
                c = ctx or {
                    "text_buffer": "",
                    "current_tool_name": None,
                    "current_tool_call_id": None,
                    "tool_start_times": {},
                    "usage_cumulative": {"input_tokens": 0, "output_tokens": 0},
                    "usage_seen_run_ids": set(),
                }
                try:
                    for line in bridge.process_item({"type": "__tw_error__", "content": str(e)}, b, c):
                        yield line
                    for line in bridge.process_item(
                        {"type": "__tw_finish__", "usage": {}, "finish_reason": "error"},
                        b,
                        c,
                    ):
                        yield line
                    for line in bridge.finalize():
                        yield line
                except Exception:
                    logging.exception("failed to emit SSE after QA exception")
            cls._active_streams.pop(session_id, None)

    @classmethod
    async def exec_test_case_resume(
        cls,
        session_id: str,
        selected_point_names: List[str],
        current_user: CurrentUser,
        db: AsyncSession,
    ) -> AsyncGenerator[str, None]:
        """
        测试用例生成第二阶段：用户采纳测试点并二次确认后，流式生成具体用例。
        """
        task_cancelled = False
        names = [n for n in (selected_point_names or []) if isinstance(n, str) and n.strip()]
        preview = "、".join(names[:15])
        if len(names) > 15:
            preview += "…"
        user_text = f"确认生成测试用例，已采纳 {len(names)} 个测试点：{preview}"

        builder: Optional[AssistantMessageBuilder] = None
        bridge: Optional[LangGraphSseBridge] = None
        ctx: Dict[str, Any] = {}

        try:
            await ChatService.get_or_create_session(
                user_id=current_user.user_id,
                session_id=session_id,
                title=user_text[:100],
                extra={"qa_type": IntentEnum.TEST_CASE_QA.value[0]},
                db=db,
            )

            await ChatService.save_message(
                session_id=session_id,
                user_id=current_user.user_id,
                role="user",
                content=UserMessageBuilder(content=user_text).serialize(),
                extra={
                    "qa_type": IntentEnum.TEST_CASE_QA.value[0],
                    "test_case_resume": True,
                    "selected_point_names": names,
                },
                db=db,
            )

            logger.info(
                f"exec_test_case_resume 流式上游开始 session_id={session_id} user_id={current_user.user_id} point_count={len(names)}"
            )

            agent_generator = case_coordinator.resume_agent(session_id, selected_point_names=names)

            bridge = LangGraphSseBridge(
                session_id,
                emit_langfuse_session_hint=LangfuseConfig.langfuse_tracing_enabled,
                stop_token=StopTokenService.create(session_id, int(current_user.user_id)),
            )
            builder = AssistantMessageBuilder(
                session_id=session_id,
                message_id=bridge.assistant_message_id,
            )
            ctx = _new_stream_ctx()
            tc_qa = IntentEnum.TEST_CASE_QA.value[0]
            cls._active_streams[session_id] = _ActiveStreamState(
                builder=builder,
                ctx=ctx,
                qa_type=tc_qa,
            )
            if await _insert_streaming_assistant_skeleton(
                bridge.assistant_message_id, session_id, current_user.user_id
            ):
                ctx["_assistant_db_id"] = bridge.assistant_message_id

            ka_sec = float(StreamConfig.sse_keepalive_interval_seconds)
            async for sse_line in _yield_sse_from_agent_bridge(
                agent_generator,
                bridge=bridge,
                builder=builder,
                ctx=ctx,
                session_id=session_id,
                user_id=current_user.user_id,
                qa_type=tc_qa,
                keepalive_seconds=ka_sec,
                langfuse_thread_id=f"case_graph_{session_id}",
            ):
                yield sse_line

            async for sse_line in _finalize_sse_bridge_stream(
                bridge, builder, ctx, session_id, current_user.user_id
            ):
                yield sse_line

            if not task_cancelled and not ctx.get("user_stopped"):
                await _finalize_streaming_assistant(
                    builder=builder,
                    bridge=bridge,
                    ctx=ctx,
                    session_id=session_id,
                    user_id=current_user.user_id,
                    qa_type=tc_qa,
                )

            logger.info(
                f"exec_test_case_resume 流式正常结束 session_id={session_id} "
                f"assistant_message_id={bridge.assistant_message_id if bridge else ''} "
                f"finish_reason={bridge.last_finish_reason if bridge else ''}"
            )

            cls._active_streams.pop(session_id, None)

        except asyncio.CancelledError:
            task_cancelled = True
            logger.info(
                f"exec_test_case_resume 流式被取消(CancelledError) session_id={session_id} "
                f"user_id={current_user.user_id} assistant_db_id={(ctx or {}).get('_assistant_db_id')} "
                f"user_stopped={bool((ctx or {}).get('user_stopped'))}"
            )
            tc_qa = IntentEnum.TEST_CASE_QA.value[0]
            await _handle_stream_client_disconnect(
                session_id=session_id,
                qa_type=tc_qa,
                user_id=current_user.user_id,
                ctx=ctx or {},
                builder=builder,
                log_label="exec_test_case_resume",
            )
            cls._active_streams.pop(session_id, None)
            raise

        except GeneratorExit:
            task_cancelled = True
            logger.info(
                f"exec_test_case_resume 流式消费者断开(GeneratorExit) session_id={session_id} "
                f"user_id={current_user.user_id} assistant_db_id={(ctx or {}).get('_assistant_db_id')}"
            )
            tc_qa = IntentEnum.TEST_CASE_QA.value[0]
            await _handle_stream_client_disconnect(
                session_id=session_id,
                qa_type=tc_qa,
                user_id=current_user.user_id,
                ctx=ctx or {},
                builder=builder,
                log_label="exec_test_case_resume",
            )
            cls._active_streams.pop(session_id, None)
            raise

        except Exception as e:
            logging.exception(f"测试用例 resume 异常: {e}")
            aid = (ctx or {}).get("_assistant_db_id")
            tc_qa = IntentEnum.TEST_CASE_QA.value[0]
            stream_state = cls._active_streams.get(session_id)
            persist_b = stream_state.builder if stream_state else builder
            _flush_ctx_text_buffer(ctx, persist_b)
            err_text = str(e)[:8000]
            if persist_b is not None and not persist_b.is_empty():
                try:
                    await _persist_assistant(
                        _assistant_content_for_persist(persist_b, error_detail=err_text),
                        session_id,
                        current_user.user_id,
                        status="error",
                        extra=_build_assistant_persist_extra(
                            qa_type=tc_qa,
                            error_message=err_text,
                        ),
                        assistant_message_id=aid,
                    )
                except Exception:
                    logger.exception(
                        f"测试用例 resume 异常路径 assistant 落库失败: session_id={session_id} user_id={current_user.user_id}"
                    )
            elif aid:
                try:
                    await _persist_assistant(
                        _assistant_content_for_persist(None, error_detail=err_text),
                        session_id,
                        current_user.user_id,
                        status="error",
                        extra=_build_assistant_persist_extra(
                            qa_type=tc_qa,
                            error_message=err_text,
                        ),
                        assistant_message_id=aid,
                    )
                except Exception:
                    logger.exception(
                        f"测试用例 resume 异常路径空内容落库失败: session_id={session_id} user_id={current_user.user_id}"
                    )
            if bridge is not None:
                b = persist_b if persist_b is not None else AssistantMessageBuilder(session_id=session_id)
                c = ctx or {
                    "text_buffer": "",
                    "current_tool_name": None,
                    "current_tool_call_id": None,
                    "tool_start_times": {},
                    "usage_cumulative": {"input_tokens": 0, "output_tokens": 0},
                    "usage_seen_run_ids": set(),
                }
                try:
                    for line in bridge.process_item({"type": "__tw_error__", "content": str(e)}, b, c):
                        yield line
                    for line in bridge.process_item(
                        {"type": "__tw_finish__", "usage": {}, "finish_reason": "error"},
                        b,
                        c,
                    ):
                        yield line
                    for line in bridge.finalize():
                        yield line
                except Exception:
                    logging.exception("failed to emit SSE after test case resume exception")
            cls._active_streams.pop(session_id, None)

    @classmethod
    async def export_test_case_markdown(
        cls,
        session_id: str,
        current_user: CurrentUser,
        db: AsyncSession,
        test_cases: Optional[List[Dict[str, Any]]] = None,
        query: Optional[str] = None,
    ) -> tuple[str, str]:
        """
        导出测试用例 Markdown 报告。

        Returns:
            (markdown 正文, 建议下载文件名)
        """
        from fastapi import HTTPException

        session = await ChatService.get_session_by_id(
            session_id, current_user.user_id, db
        )
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")

        md = case_coordinator.get_export_markdown(
            session_id,
            test_cases=test_cases,
            query=query,
        )
        if not md:
            raise HTTPException(
                status_code=404,
                detail="暂无可导出的测试用例，请先生成用例",
            )

        safe_title = re.sub(
            r"[^\w\u4e00-\u9fff\-]+",
            "_",
            (session.title or "测试用例").strip(),
        )[:60] or "测试用例"
        return md, f"{safe_title}.md"

    @classmethod
    async def stop_chat(cls, session_id, qa_type, current_user: CurrentUser):
        """用户主动停止：权威落库后 cancel_task；流式协程见 ctx.user_stopped 跳过二次落库。"""
        stream_state = cls._active_streams.get(session_id)
        builder = stream_state.builder if stream_state else None
        ctx = stream_state.ctx if stream_state else {}
        if stream_state is not None:
            stream_state.user_stopped = True
            ctx["user_stopped"] = True

        logger.info(
            f"stop_chat 处理 session_id={session_id} qa_type={qa_type} user_id={current_user.user_id} "
            f"has_active_stream={stream_state is not None}"
        )

        aid = (
            (getattr(builder, "message_id", None) or None)
            if builder
            else ctx.get("_assistant_db_id")
        )
        _flush_ctx_text_buffer(ctx, builder)

        persist_extra = {"qa_type": qa_type, "finish_reason": "stopped"}
        if builder and (aid or not builder.is_empty()):
            try:
                snapshot = builder.to_dict() if not builder.is_empty() else {"version": 1, "parts": []}
                content = append_user_stop_notice_to_content(snapshot)
                await _persist_assistant(
                    content,
                    session_id,
                    current_user.user_id,
                    status="partial",
                    extra=persist_extra,
                    assistant_message_id=aid,
                )
            except Exception:
                logger.exception(
                    f"stop_chat 时 assistant 消息落库失败: session_id={session_id} user_id={current_user.user_id}"
                )
        elif aid:
            try:
                content = append_user_stop_notice_to_content({"version": 1, "parts": []})
                await _persist_assistant(
                    content,
                    session_id,
                    current_user.user_id,
                    status="partial",
                    extra=persist_extra,
                    assistant_message_id=aid,
                )
            except Exception:
                logger.exception(
                    f"stop_chat 时 assistant 空内容落库失败: session_id={session_id} user_id={current_user.user_id}"
                )

        if qa_type == IntentEnum.COMMON_QA.value[0]:
            status = await common_agent.cancel_task(session_id)
            logger.info(f"stop_chat cancel_task COMMON_QA session_id={session_id} marked={status}")
            return status, "停止成功"
        if qa_type == IntentEnum.FAULT_OPERATION_QA.value[0]:
            status = await fault_agent.cancel_task(session_id)
            logger.info(f"stop_chat cancel_task FAULT_OPERATION session_id={session_id} marked={status}")
            return status, "停止成功"
        if qa_type == IntentEnum.TEST_CASE_QA.value[0]:
            status, msg = await case_coordinator.cancel_task(session_id)
            logger.info(f"stop_chat cancel_task TEST_CASE session_id={session_id} ok={status} msg={msg}")
            return status, msg
        if qa_type == IntentEnum.DEEP_RESEARCH_QA.value[0]:
            status = await deep_research_agent.cancel_task(session_id)
            logger.info(f"stop_chat cancel_task DEEP_RESEARCH session_id={session_id} marked={status}")
            return status, "停止成功"

        logger.warning(f"stop_chat 未知 qa_type session_id={session_id} qa_type={qa_type}")
        return False, "未知的 qa_type"


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

