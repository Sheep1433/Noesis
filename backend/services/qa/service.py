"""QaService — QA orchestration (exec_query / resume / export / stop_chat)."""

import asyncio
import logging
import re
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from agent.mcp.loader import load_mcp_tools_by_names
from config.database import AsyncSessionLocal
from config.env import ChatAttachmentConfig, LangfuseConfig, StreamConfig
from constants.code_enum import IntentEnum
from schemas.login_vo import CurrentUser
from schemas.qa_vo import QaQueryRequest
from services.chat_service import ChatService
from services.chat_attachment_service import ChatAttachmentService
from services.mention_resolve_service import MentionResolveService
from domain.chat.streaming.langgraph_sse import LangGraphSseBridge
from common.logging import logger
from domain.chat.message_builder import AssistantMessageBuilder, UserMessageBuilder
from domain.chat.streaming.failure_notice import append_user_stop_notice_to_content
from llm.catalog import get_default_model_id

from services.qa.helpers import (
    ACTIVE_STREAMS,
    _ActiveStreamState,
    _assistant_content_for_persist,
    _build_assistant_persist_extra,
    _finalize_sse_bridge_stream,
    _finalize_streaming_assistant,
    _flush_ctx_text_buffer,
    _handle_stream_client_disconnect,
    _insert_streaming_assistant_skeleton,
    _mark_stream_persist_finalized,
    _new_stream_ctx,
    _persist_assistant,
    _persist_hitl_pending_assistant,
    _register_active_stream,
    _resolve_enabled_skills_for_query,
    _resolve_kb_settings_for_query,
    _resolve_mcp_servers_for_query,
    _resolve_model_for_query,
    _resolved_model_name,
    _unregister_active_stream,
    _yield_sse_from_agent_bridge,
    case_coordinator,
    common_agent,
    fault_agent,
    super_agent,
)


class QaService:
    # session_id -> 流式状态（stop 时权威落库）；与 helpers.ACTIVE_STREAMS 同一 dict
    _active_streams = ACTIVE_STREAMS

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

        if ChatAttachmentConfig.enabled and req_obj.file_dict:
            ChatAttachmentService.validate_message_file_count(req_obj.file_dict)

        session_id = req_obj.chat_id or str(uuid.uuid4())
        task_cancelled = False
        user_text = (req_obj.query or "").strip()
        builder: Optional[AssistantMessageBuilder] = None
        bridge: Optional[LangGraphSseBridge] = None
        ctx: Dict[str, Any] = {}

        resolved_mentions = MentionResolveService.resolve(
            mentions=req_obj.mentions,
            qa_type=req_obj.qa_type,
            user_id=str(current_user.user_id),
            session_id=session_id,
        )
        agent_query = clean_query
        if resolved_mentions.prompt_block:
            agent_query = (
                f"{clean_query}\n\n{resolved_mentions.prompt_block}".strip()
                if clean_query
                else resolved_mentions.prompt_block
            )

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
                user_extra: Dict[str, Any] = {
                    "qa_type": req_obj.qa_type,
                    "file_dict": req_obj.file_dict,
                }
                if resolved_mentions.persistence:
                    user_extra["mentions"] = resolved_mentions.persistence
                await ChatService.save_message(
                    session_id=session_id,
                    user_id=current_user.user_id,
                    role="user",
                    content=UserMessageBuilder(content=user_text).serialize(),
                    extra=user_extra,
                    db=db,
                )

            logger.info(
                f"exec_query 流式上游开始 session_id={session_id} qa_type={req_obj.qa_type} user_id={current_user.user_id}"
            )

            resolved_model_id = get_default_model_id()
            resolved_model_name = _resolved_model_name(resolved_model_id)
            if req_obj.qa_type != IntentEnum.TEST_CASE_QA.value[0]:
                resolved_model_id = await _resolve_model_for_query(
                    session_id=session_id,
                    user_id=str(current_user.user_id),
                    request_model_id=req_obj.model_id,
                    db=db,
                )
                resolved_model_name = _resolved_model_name(resolved_model_id)

            # 根据 qa_type 选择 agent 并执行
            kb_collections: List[str] = []
            kb_search_enabled = True
            if req_obj.qa_type == IntentEnum.COMMON_QA.value[0]:
                kb_collections, kb_search_enabled = await _resolve_kb_settings_for_query(
                    session_id=session_id,
                    user_id=str(current_user.user_id),
                    request_kb_collections=req_obj.kb_collections,
                    request_kb_search_enabled=req_obj.kb_search_enabled,
                    db=db,
                )

            mcp_server_ids: List[str] = []
            enabled_skills: Optional[List[str]] = None
            if req_obj.qa_type != IntentEnum.TEST_CASE_QA.value[0]:
                mcp_server_ids = await _resolve_mcp_servers_for_query(
                    session_id=session_id,
                    user_id=str(current_user.user_id),
                    qa_type=req_obj.qa_type,
                    request_mcp_servers=req_obj.mcp_servers,
                    db=db,
                )
                enabled_skills = await _resolve_enabled_skills_for_query(
                    session_id=session_id,
                    user_id=str(current_user.user_id),
                    request_enabled_skills=req_obj.enabled_skills,
                    db=db,
                )
                if resolved_mentions.skill_ids and enabled_skills is not None:
                    enabled_skills = list(
                        dict.fromkeys([*enabled_skills, *resolved_mentions.skill_ids]),
                    )

            mcp_tools: List[Any] = []
            if mcp_server_ids:
                mcp_tools = await load_mcp_tools_by_names(
                    mcp_server_ids,
                    user_id=str(current_user.user_id),
                )

            if req_obj.qa_type == IntentEnum.COMMON_QA.value[0]:
                agent_generator = common_agent.run_agent(
                    agent_query,
                    session_id=session_id,
                    current_user=current_user,
                    file_list=req_obj.file_dict,
                    qa_type=req_obj.qa_type,
                    kb_collections=kb_collections or None,
                    kb_search_enabled=kb_search_enabled,
                    model_id=resolved_model_id,
                    mcp_tools=mcp_tools or None,
                    db=db,
                )
            elif req_obj.qa_type == IntentEnum.FAULT_OPERATION_QA.value[0]:
                agent_generator = fault_agent.run_agent(
                    agent_query,
                    session_id=session_id,
                    current_user=current_user,
                    file_list=req_obj.file_dict,
                    qa_type=req_obj.qa_type,
                    model_id=resolved_model_id,
                    mcp_tools=mcp_tools,
                )
            elif req_obj.qa_type == IntentEnum.TEST_CASE_QA.value[0]:
                agent_generator = case_coordinator.run_agent(
                    agent_query,
                    session_id,
                    req_obj.file_dict,
                    qa_type=req_obj.qa_type,
                )
            elif req_obj.qa_type == IntentEnum.SUPER_AGENT_QA.value[0]:
                agent_generator = super_agent.run_agent(
                    agent_query,
                    session_id=session_id,
                    current_user=current_user,
                    file_list=req_obj.file_dict,
                    qa_type=req_obj.qa_type,
                    model_id=resolved_model_id,
                    mcp_tools=mcp_tools or None,
                    enabled_skills=enabled_skills,
                    db=db,
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
            )
            builder = AssistantMessageBuilder(
                session_id=session_id,
                message_id=bridge.assistant_message_id,
            )
            ctx = _new_stream_ctx()
            _register_active_stream(
                session_id,
                _ActiveStreamState(
                    builder=builder,
                    ctx=ctx,
                    qa_type=req_obj.qa_type,
                    model_name=resolved_model_name,
                ),
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
                if bridge.last_finish_reason == "hitl_pending":
                    await _persist_hitl_pending_assistant(
                        builder=builder,
                        bridge=bridge,
                        ctx=ctx,
                        session_id=session_id,
                        user_id=current_user.user_id,
                        qa_type=req_obj.qa_type,
                        model=resolved_model_name,
                    )
                else:
                    await _finalize_streaming_assistant(
                        builder=builder,
                        bridge=bridge,
                        ctx=ctx,
                        session_id=session_id,
                        user_id=current_user.user_id,
                        qa_type=req_obj.qa_type,
                        model=resolved_model_name,
                    )

            logger.info(
                f"exec_query 流式正常结束 session_id={session_id} qa_type={req_obj.qa_type} "
                f"assistant_message_id={bridge.assistant_message_id if bridge else ''} "
                f"finish_reason={bridge.last_finish_reason if bridge else ''}"
            )

            _unregister_active_stream(session_id)

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
            _unregister_active_stream(session_id)
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
            _unregister_active_stream(session_id)
            raise

        except Exception as e:
            logging.exception(f"QA服务异常: {e}")
            aid = (ctx or {}).get("_assistant_db_id")
            stream_state = cls._active_streams.get(session_id)
            persist_b = stream_state.builder if stream_state else builder
            persist_model = stream_state.model_name if stream_state else None
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
                            model=persist_model,
                        ),
                        assistant_message_id=aid,
                    )
                    _mark_stream_persist_finalized(ctx)
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
                            model=persist_model,
                        ),
                        assistant_message_id=aid,
                    )
                    _mark_stream_persist_finalized(ctx)
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
            _unregister_active_stream(session_id)

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
            )
            builder = AssistantMessageBuilder(
                session_id=session_id,
                message_id=bridge.assistant_message_id,
            )
            ctx = _new_stream_ctx()
            tc_qa = IntentEnum.TEST_CASE_QA.value[0]
            _register_active_stream(
                session_id,
                _ActiveStreamState(
                    builder=builder,
                    ctx=ctx,
                    qa_type=tc_qa,
                ),
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

            _unregister_active_stream(session_id)

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
            _unregister_active_stream(session_id)
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
            _unregister_active_stream(session_id)
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
                    _mark_stream_persist_finalized(ctx)
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
                    _mark_stream_persist_finalized(ctx)
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
            _unregister_active_stream(session_id)

    @classmethod
    async def exec_hitl_resume(
        cls,
        session_id: str,
        *,
        interrupt_id: str,
        decisions: List[Dict[str, Any]],
        grant_scope: Optional[str],
        current_user: CurrentUser,
        db: AsyncSession,
    ) -> AsyncGenerator[str, None]:
        """HITL resume：新开 SSE，续写同一 assistant_message_id。"""
        from domain.chat.hitl.pending import pending_hitl
        from agent.guardrails.session_grants import session_grants
        from models.chat_models import TChatMessage
        from sqlalchemy import and_, select

        task_cancelled = False
        builder: Optional[AssistantMessageBuilder] = None
        bridge: Optional[LangGraphSseBridge] = None
        ctx: Dict[str, Any] = {}
        qa_type = IntentEnum.SUPER_AGENT_QA.value[0]

        pending = pending_hitl.get(session_id)
        if (
            pending is None
            or pending.interrupt_id != interrupt_id
            or pending.user_id != str(current_user.user_id)
        ):
            br = LangGraphSseBridge(session_id)
            c: Dict[str, Any] = {}
            for line in br.process_item(
                {"type": "__tw_error__", "content": "无匹配的 pending HITL"},
                None,
                c,
            ):
                yield line
            for line in br.process_item(
                {"type": "__tw_finish__", "finish_reason": "error", "usage": {}},
                None,
                c,
            ):
                yield line
            for line in br.finalize():
                yield line
            return

        if pending_hitl.is_expired(pending):
            pending_hitl.clear(session_id)
            br = LangGraphSseBridge(
                session_id,
                assistant_message_id=pending.assistant_message_id,
            )
            c = {}
            for line in br.process_item(
                {"type": "__tw_error__", "content": "HITL 已超时"},
                None,
                c,
            ):
                yield line
            for line in br.process_item(
                {"type": "__tw_finish__", "finish_reason": "error", "usage": {}},
                None,
                c,
            ):
                yield line
            for line in br.finalize():
                yield line
            return

        if grant_scope == "session":
            # 仅网络类 execute 可 grant；memory 写入不在此路径授予
            session_grants.grant(session_id, "network_execute")

        decision_payloads = []
        for d in decisions:
            item: Dict[str, Any] = {"type": d.get("type")}
            if d.get("message") is not None:
                item["message"] = d["message"]
            decision_payloads.append(item)

        aid = pending.assistant_message_id
        actions = list(pending.action_requests or [])
        pending_hitl.pop_if_match(session_id, interrupt_id)
        from domain.chat.hitl.timeout import cancel_hitl_timeout

        cancel_hitl_timeout(session_id)

        try:
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
            ctx = _new_stream_ctx()
            ctx["_assistant_db_id"] = aid
            _register_active_stream(
                session_id,
                _ActiveStreamState(
                    builder=builder,
                    ctx=ctx,
                    qa_type=qa_type,
                    model_name=None,
                ),
            )

            # reject/respond 不经 on_tool_end：先合成 tool-output 与 hitl 状态
            from domain.chat.streaming.langgraph_sse import _format_sse

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
                    yield _format_sse(
                        "tool-output-available",
                        {
                            "type": "tool-output-available",
                            "message_id": aid,
                            "tool_call_id": tool_call_id,
                            "name": name,
                            "output": msg_text,
                            "status": "error",
                            "error": msg_text,
                        },
                    )
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
                    yield _format_sse(
                        "tool-output-available",
                        {
                            "type": "tool-output-available",
                            "message_id": aid,
                            "tool_call_id": tool_call_id,
                            "name": name,
                            "output": answer,
                            "status": "success",
                        },
                    )

            agent_generator = super_agent.resume_agent(
                session_id=session_id,
                decisions=decision_payloads,
                current_user=current_user,
                qa_type=qa_type,
                db=db,
                message_id=aid,
            )

            ka_sec = float(StreamConfig.sse_keepalive_interval_seconds)
            async for sse_line in _yield_sse_from_agent_bridge(
                agent_generator,
                bridge=bridge,
                builder=builder,
                ctx=ctx,
                session_id=session_id,
                user_id=current_user.user_id,
                qa_type=qa_type,
                keepalive_seconds=ka_sec,
            ):
                yield sse_line

            async for sse_line in _finalize_sse_bridge_stream(
                bridge, builder, ctx, session_id, current_user.user_id
            ):
                yield sse_line

            if not task_cancelled and not ctx.get("user_stopped"):
                if bridge.last_finish_reason == "hitl_pending":
                    await _persist_hitl_pending_assistant(
                        builder=builder,
                        bridge=bridge,
                        ctx=ctx,
                        session_id=session_id,
                        user_id=current_user.user_id,
                        qa_type=qa_type,
                    )
                else:
                    await _finalize_streaming_assistant(
                        builder=builder,
                        bridge=bridge,
                        ctx=ctx,
                        session_id=session_id,
                        user_id=current_user.user_id,
                        qa_type=qa_type,
                    )

            _unregister_active_stream(session_id)

        except asyncio.CancelledError:
            task_cancelled = True
            await _handle_stream_client_disconnect(
                session_id=session_id,
                qa_type=qa_type,
                user_id=current_user.user_id,
                ctx=ctx or {},
                builder=builder,
                log_label="exec_hitl_resume",
            )
            _unregister_active_stream(session_id)
            raise
        except Exception as e:
            logging.exception(f"HITL resume 异常: {e}")
            if bridge is not None:
                b = builder or AssistantMessageBuilder(session_id=session_id)
                c = ctx or {}
                try:
                    for line in bridge.process_item(
                        {"type": "__tw_error__", "content": str(e)}, b, c
                    ):
                        yield line
                    for line in bridge.process_item(
                        {"type": "__tw_finish__", "finish_reason": "error", "usage": {}},
                        b,
                        c,
                    ):
                        yield line
                    for line in bridge.finalize():
                        yield line
                except Exception:
                    logging.exception("failed to emit SSE after HITL resume exception")
            _unregister_active_stream(session_id)

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
        from domain.chat.delivery.orchestrator import CancelReason, run_lifecycle

        stream_state = cls._active_streams.get(session_id)
        builder = stream_state.builder if stream_state else None
        ctx = stream_state.ctx if stream_state else {}
        if stream_state is not None:
            stream_state.user_stopped = True
            ctx["user_stopped"] = True
        run_lifecycle.notify_cancel(session_id, CancelReason.USER_STOP)

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
        if qa_type == IntentEnum.SUPER_AGENT_QA.value[0]:
            status = await super_agent.cancel_task(session_id)
            logger.info(f"stop_chat cancel_task SUPER_AGENT session_id={session_id} marked={status}")
            return status, "停止成功"

        logger.warning(f"stop_chat 未知 qa_type session_id={session_id} qa_type={qa_type}")
        return False, "未知的 qa_type"
