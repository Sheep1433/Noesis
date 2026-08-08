"""QaService — Run-managed QA orchestration (exec_query / resume / export)."""

import asyncio
import logging
import re
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.errors.exceptions import NotFoundException, ServiceException

from noesis.mcp.loader import load_mcp_tools_by_names
from noesis.errors.exceptions import NotFoundException, ServiceException

from noesis.storage.postgres.manager import pg_manager
from noesis.errors.exceptions import NotFoundException, ServiceException

from noesis.config.env import ChatAttachmentConfig, LangfuseConfig, StreamConfig
from noesis.errors.exceptions import NotFoundException, ServiceException

from noesis.config.code_enum import IntentEnum
from noesis.errors.exceptions import NotFoundException, ServiceException

from noesis.schemas.login_vo import CurrentUser
from noesis.errors.exceptions import NotFoundException, ServiceException

from noesis.schemas.qa_vo import QaQueryRequest
from noesis.errors.exceptions import NotFoundException, ServiceException

from noesis.services.chat_service import ChatService
from noesis.errors.exceptions import NotFoundException, ServiceException

from noesis.services.chat_attachment_service import ChatAttachmentService
from noesis.errors.exceptions import NotFoundException, ServiceException

from noesis.services.mention_resolve_service import MentionResolveService
from noesis.errors.exceptions import NotFoundException, ServiceException

from noesis.domain.chat.streaming.langgraph_sse import LangGraphSseBridge
from noesis.errors.exceptions import NotFoundException, ServiceException

from noesis.runtime.logging import logger
from noesis.errors.exceptions import NotFoundException, ServiceException

from noesis.domain.chat.message_builder import AssistantMessageBuilder
from noesis.errors.exceptions import NotFoundException, ServiceException

from noesis.domain.chat.tool_state import ToolState
from noesis.errors.exceptions import NotFoundException, ServiceException

from noesis.llm.catalog import get_default_model_id

from noesis.errors.exceptions import NotFoundException, ServiceException

from noesis.services.qa.helpers import (
    _finalize_sse_bridge_stream,
    _new_stream_ctx,
    _resolve_enabled_skills_for_query,
    _resolve_kb_settings_for_query,
    _resolve_mcp_servers_for_query,
    _resolve_model_for_query,
    _yield_sse_from_agent_bridge,
    case_coordinator,
    common_agent,
    fault_agent,
    super_agent,
)


class QaService:
    @classmethod
    async def exec_query(
        cls,
        req_obj: QaQueryRequest,
        current_user: CurrentUser,
        db: AsyncSession,
        *,
        assistant_message_id: Optional[str] = None,
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
            logger.info(
                f"exec_query 流式上游开始 session_id={session_id} qa_type={req_obj.qa_type} user_id={current_user.user_id}"
            )

            resolved_model_id = get_default_model_id()
            if req_obj.qa_type != IntentEnum.TEST_CASE_QA.value[0]:
                resolved_model_id = await _resolve_model_for_query(
                    session_id=session_id,
                    user_id=str(current_user.user_id),
                    request_model_id=req_obj.model_id,
                    db=db,
                )

            # 根据 qa_type 选择 agent 并执行
            kb_collections: List[str] = []
            kb_search_enabled = True
            if req_obj.qa_type in (IntentEnum.COMMON_QA.value[0], IntentEnum.SUPER_AGENT_QA.value[0]):
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
                    kb_collections=kb_collections or None,
                    kb_search_enabled=kb_search_enabled,
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
                assistant_message_id=assistant_message_id,
            )
            builder = AssistantMessageBuilder(
                session_id=session_id,
                message_id=bridge.assistant_message_id,
            )
            ctx = _new_stream_ctx()
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

            logger.info(
                f"exec_query 流式正常结束 session_id={session_id} qa_type={req_obj.qa_type} "
                f"assistant_message_id={bridge.assistant_message_id if bridge else ''} "
                f"finish_reason={bridge.last_finish_reason if bridge else ''}"
            )

        except asyncio.CancelledError:
            logger.info(
                f"exec_query 流式任务被取消(CancelledError) session_id={session_id} qa_type={req_obj.qa_type} "
                f"user_id={current_user.user_id} assistant_db_id={(ctx or {}).get('_assistant_db_id')} "
                f"user_stopped={bool((ctx or {}).get('user_stopped'))}"
            )
            raise

        except GeneratorExit:
            logger.info(
                f"exec_query 流式消费者断开(GeneratorExit) session_id={session_id} qa_type={req_obj.qa_type} "
                f"user_id={current_user.user_id} assistant_db_id={(ctx or {}).get('_assistant_db_id')}"
            )
            raise

        except Exception as e:
            logging.exception(f"QA服务异常: {e}")
            if bridge is not None:
                b = builder or AssistantMessageBuilder(session_id=session_id)
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

    @classmethod
    async def exec_test_case_resume(
        cls,
        session_id: str,
        selected_point_names: List[str],
        current_user: CurrentUser,
        db: AsyncSession,
        *,
        assistant_message_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """
        测试用例生成第二阶段：用户采纳测试点并二次确认后，流式生成具体用例。
        """
        names = [n for n in (selected_point_names or []) if isinstance(n, str) and n.strip()]
        builder: Optional[AssistantMessageBuilder] = None
        bridge: Optional[LangGraphSseBridge] = None
        ctx: Dict[str, Any] = {}

        try:
            logger.info(
                f"exec_test_case_resume 流式上游开始 session_id={session_id} user_id={current_user.user_id} point_count={len(names)}"
            )

            agent_generator = case_coordinator.resume_agent(session_id, selected_point_names=names)

            bridge = LangGraphSseBridge(
                session_id,
                emit_langfuse_session_hint=LangfuseConfig.langfuse_tracing_enabled,
                assistant_message_id=assistant_message_id,
            )
            builder = AssistantMessageBuilder(
                session_id=session_id,
                message_id=bridge.assistant_message_id,
            )
            from sqlalchemy import select
            from noesis.storage.postgres.models.chat import TChatMessage

            async with pg_manager.get_async_session_context() as persist_db:
                result = await persist_db.execute(
                    select(TChatMessage).where(TChatMessage.id == bridge.assistant_message_id)
                )
                existing = result.scalar_one_or_none()
                if existing is not None and isinstance(existing.content, dict):
                    builder.load_from_content_dict(existing.content)
            ctx = _new_stream_ctx()
            tc_qa = IntentEnum.TEST_CASE_QA.value[0]
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

            logger.info(
                f"exec_test_case_resume 流式正常结束 session_id={session_id} "
                f"assistant_message_id={bridge.assistant_message_id if bridge else ''} "
                f"finish_reason={bridge.last_finish_reason if bridge else ''}"
            )

        except asyncio.CancelledError:
            logger.info(
                f"exec_test_case_resume 流式被取消(CancelledError) session_id={session_id} "
                f"user_id={current_user.user_id} assistant_db_id={(ctx or {}).get('_assistant_db_id')} "
                f"user_stopped={bool((ctx or {}).get('user_stopped'))}"
            )
            raise

        except GeneratorExit:
            logger.info(
                f"exec_test_case_resume 流式消费者断开(GeneratorExit) session_id={session_id} "
                f"user_id={current_user.user_id} assistant_db_id={(ctx or {}).get('_assistant_db_id')}"
            )
            raise

        except Exception as e:
            logging.exception(f"测试用例 resume 异常: {e}")
            if bridge is not None:
                b = builder or AssistantMessageBuilder(session_id=session_id)
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

    @classmethod
    async def exec_hitl_resume(
        cls,
        *,
        pending,
        decisions: List[Dict[str, Any]],
        grant_scope: Optional[str],
        current_user: CurrentUser,
        db: AsyncSession,
    ) -> AsyncGenerator[str, None]:
        """HITL resume：新开 SSE，续写同一 assistant_message_id。"""
        from noesis.guardrails.session_grants import session_grants
        from noesis.storage.postgres.models.chat import TChatMessage
        from sqlalchemy import and_, select

        builder: Optional[AssistantMessageBuilder] = None
        bridge: Optional[LangGraphSseBridge] = None
        ctx: Dict[str, Any] = {}
        qa_type = IntentEnum.SUPER_AGENT_QA.value[0]
        session_id = pending.session_id

        if pending.user_id != str(current_user.user_id):
            raise PermissionError("HITL pending owner mismatch")
        if pending.expires_at > 0 and pending.expires_at <= time.time():
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

        try:
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
            ctx = _new_stream_ctx()
            ctx["_assistant_db_id"] = aid

            # reject/respond 不经 on_tool_end：先合成 tool-output 与 hitl 状态
            from noesis.domain.chat.streaming.langgraph_sse import _format_sse

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
                    yield _format_sse(
                        "tool-output-available",
                        {
                            "type": "tool-output-available",
                            "message_id": aid,
                            "tool_call_id": tool_call_id,
                            "name": name,
                            "output": msg_text,
                            "status": "error",
                            "state": ToolState.REJECTED.value,
                            "outcome": "rejected",
                            "error": msg_text,
                        },
                    )
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
                    yield _format_sse(
                        "tool-output-available",
                        {
                            "type": "tool-output-available",
                            "message_id": aid,
                            "tool_call_id": tool_call_id,
                            "name": name,
                            "output": answer,
                            "status": "success",
                            "state": ToolState.SUCCEEDED.value,
                            "outcome": "ok",
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

        except asyncio.CancelledError:
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

        session = await ChatService.get_session_by_id(
            session_id, current_user.user_id, db
        )
        if not session:
            raise NotFoundException(message="会话不存在")

        md = case_coordinator.get_export_markdown(
            session_id,
            test_cases=test_cases,
            query=query,
        )
        if not md:
            raise NotFoundException(message="暂无可导出的测试用例，请先生成用例")

        safe_title = re.sub(
            r"[^\w\u4e00-\u9fff\-]+",
            "_",
            (session.title or "测试用例").strip(),
        )[:60] or "测试用例"
        return md, f"{safe_title}.md"
