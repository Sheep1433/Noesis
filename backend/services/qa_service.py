"""
QaService - 问答服务

流式输出为 Noesis SSE 文本帧（str）；上游为 LangGraph astream_events + 少量控制 dict，由 LangGraphSseBridge 转换。
"""
import asyncio
import logging
import re
import uuid
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
from utils.langfuse_tracing import langfuse_workflow_context, merge_langfuse_runnable_config
from utils.langgraph_sse_bridge import LangGraphSseBridge
from utils.log_util import logger
from utils.message_builder import AssistantMessageBuilder, UserMessageBuilder
from utils.stream_failure_notice import append_stream_failure_notice_to_content


common_agent = GeneralQAAgent()
fault_agent = FaultOperationAgent()
deep_research_agent = DeepResearchAgent()
case_coordinator = CaseCoordinator()

SSE_COMMENT_KEEPALIVE = ": keepalive\n\n"


class _KeepaliveTick:
    """``exec_query`` 空闲保活占位，与上游 item 区分。"""


KEEPALIVE_TICK = _KeepaliveTick()


async def _iter_agent_items_with_keepalive(
    agent_generator: AsyncGenerator[Any, None],
    keepalive_seconds: float,
) -> AsyncGenerator[Any, None]:
    """
    在「下一上游事件」与固定 sleep 之间竞速；sleep 先完成时产出 ``KEEPALIVE_TICK``。

    对进行中的 ``__anext__`` 使用 ``asyncio.shield``，超时只发保活、不取消上游，
    且同一 ``__anext__`` 在超时后复用同一 Future（避免每帧 ``create_task`` 切换 Context）。

    ``keepalive_seconds <= 0`` 时关闭保活，等价于 ``async for`` 透传。
    """
    if keepalive_seconds <= 0:
        async for item in agent_generator:
            yield item
        return

    agen = agent_generator.__aiter__()
    pending: Optional[asyncio.Future[Any]] = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(agen.__anext__())
            try:
                item = await asyncio.wait_for(
                    asyncio.shield(pending),
                    timeout=keepalive_seconds,
                )
            except asyncio.TimeoutError:
                yield KEEPALIVE_TICK
                continue
            except StopAsyncIteration:
                break
            pending = None
            yield item
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            try:
                await pending
            except (asyncio.CancelledError, StopAsyncIteration):
                pass


async def _iter_test_case_coordinator_stream(
    agent_generator: AsyncGenerator[Any, None],
    session_id: str,
    qa_type: str,
    keepalive_seconds: float,
) -> AsyncGenerator[Any, None]:
    """
    测试用例协调器 SSE 上游：Langfuse workflow context 须在消费 Task 内设置，
    不可放在 CaseCoordinator async generator 内（与保活 ``__anext__`` 跨 Task 冲突）。
    """
    lf_config = merge_langfuse_runnable_config(
        {"configurable": {"thread_id": f"case_graph_{session_id}"}},
        langfuse_session_id=session_id,
        qa_type=qa_type,
        enabled=LangfuseConfig.langfuse_tracing_enabled,
        langfuse_trace_id=session_id,
    )
    with langfuse_workflow_context(lf_config):
        async for raw in _iter_agent_items_with_keepalive(agent_generator, keepalive_seconds):
            yield raw


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
    """流开始前插入 assistant 骨架行（与 SSE assistantMessageId 同 id）。"""
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


class QaService:
    # 保存 session_id -> builder 的映射，用于 stop 时保存未完成的消息
    _current_builders: Dict[str, AssistantMessageBuilder] = {}

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
                    clean_query, session_id, None,
                    current_user, req_obj.file_dict,
                    qa_type=req_obj.qa_type,
                )
            elif req_obj.qa_type == IntentEnum.FAULT_OPERATION_QA.value[0]:
                agent_generator = fault_agent.run_agent(
                    clean_query, session_id, None,
                    req_obj.file_dict,
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
                    clean_query, session_id, None,
                    current_user, req_obj.file_dict,
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
                for line in br.process_item({"type": "finish", "finishReason": "error", "usage": {}}, None, ctx_err):
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
            cls._current_builders[session_id] = builder
            ctx = {
                "text_buffer": "",
                "current_tool_name": None,
                "current_tool_call_id": None,
                "tool_start_times": {},
                "usage_cumulative": {"input_tokens": 0, "output_tokens": 0},
                "usage_seen_run_ids": set(),
                "_assistant_db_id": None,
            }
            if await _insert_streaming_assistant_skeleton(
                bridge.assistant_message_id, session_id, current_user.user_id
            ):
                ctx["_assistant_db_id"] = bridge.assistant_message_id

            ka_sec = float(StreamConfig.sse_keepalive_interval_seconds)
            if req_obj.qa_type == IntentEnum.TEST_CASE_QA.value[0]:
                upstream = _iter_test_case_coordinator_stream(
                    agent_generator, session_id, req_obj.qa_type, ka_sec
                )
            else:
                upstream = _iter_agent_items_with_keepalive(agent_generator, ka_sec)
            async for raw in upstream:
                if raw is KEEPALIVE_TICK:
                    yield SSE_COMMENT_KEEPALIVE
                    continue
                item = raw
                for sse_line in bridge.process_item(item, builder, ctx):
                    yield sse_line
                if bridge.consume_persist_tick() and ctx.get("_assistant_db_id"):
                    try:
                        await _persist_assistant(
                            _assistant_content_snapshot(builder),
                            session_id,
                            current_user.user_id,
                            status="streaming",
                            assistant_message_id=ctx["_assistant_db_id"],
                        )
                    except Exception:
                        logger.exception(
                            f"assistant 流式检查点落库失败 session_id={session_id}"
                        )

            if ctx.get("text_buffer"):
                builder.append_text(ctx["text_buffer"])
                ctx["text_buffer"] = ""

            for sse_line in bridge.finalize():
                yield sse_line
                if bridge.consume_persist_tick() and ctx.get("_assistant_db_id"):
                    try:
                        await _persist_assistant(
                            _assistant_content_snapshot(builder),
                            session_id,
                            current_user.user_id,
                            status="streaming",
                            assistant_message_id=ctx["_assistant_db_id"],
                        )
                    except Exception:
                        logger.exception(
                            f"assistant 流式检查点落库失败 session_id={session_id}"
                        )

            if not task_cancelled:
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

            cls._current_builders.pop(session_id, None)

        except asyncio.CancelledError:
            task_cancelled = True
            logger.info(
                f"exec_query 流式任务被取消(CancelledError) session_id={session_id} qa_type={req_obj.qa_type} "
                f"user_id={current_user.user_id} assistant_db_id={(ctx or {}).get('_assistant_db_id')}"
            )
            aid = (ctx or {}).get("_assistant_db_id")
            if session_id in cls._current_builders:
                builder = cls._current_builders[session_id]
                if ctx.get("text_buffer") and builder:
                    builder.append_text(ctx["text_buffer"])
                    ctx["text_buffer"] = ""
                if builder and not builder.is_empty():
                    try:
                        await _persist_assistant(
                            builder.to_dict(),
                            session_id,
                            current_user.user_id,
                            status="partial",
                            extra={"qa_type": req_obj.qa_type},
                            assistant_message_id=aid,
                        )
                    except Exception:
                        logger.exception(
                            f"取消 SSE 时 assistant 消息落库失败: session_id={session_id} user_id={current_user.user_id}"
                        )
                elif aid:
                    try:
                        await _persist_assistant(
                            {"version": 1, "parts": []},
                            session_id,
                            current_user.user_id,
                            status="partial",
                            extra={"qa_type": req_obj.qa_type},
                            assistant_message_id=aid,
                        )
                    except Exception:
                        logger.exception(
                            f"取消 SSE 时 assistant 空内容落库失败: session_id={session_id} user_id={current_user.user_id}"
                        )
            cls._current_builders.pop(session_id, None)
            raise

        except Exception as e:
            logging.exception(f"QA服务异常: {e}")
            aid = (ctx or {}).get("_assistant_db_id")
            persist_b = builder
            if persist_b is None and session_id in cls._current_builders:
                persist_b = cls._current_builders[session_id]
            if ctx.get("text_buffer") and persist_b is not None:
                persist_b.append_text(ctx["text_buffer"])
                ctx["text_buffer"] = ""
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
                b = builder if builder is not None else AssistantMessageBuilder(session_id=session_id)
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
            cls._current_builders.pop(session_id, None)

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
            cls._current_builders[session_id] = builder
            ctx = {
                "text_buffer": "",
                "current_tool_name": None,
                "current_tool_call_id": None,
                "tool_start_times": {},
                "usage_cumulative": {"input_tokens": 0, "output_tokens": 0},
                "usage_seen_run_ids": set(),
                "_assistant_db_id": None,
            }
            if await _insert_streaming_assistant_skeleton(
                bridge.assistant_message_id, session_id, current_user.user_id
            ):
                ctx["_assistant_db_id"] = bridge.assistant_message_id

            ka_sec = float(StreamConfig.sse_keepalive_interval_seconds)
            async for raw in _iter_test_case_coordinator_stream(
                agent_generator,
                session_id,
                IntentEnum.TEST_CASE_QA.value[0],
                ka_sec,
            ):
                if raw is KEEPALIVE_TICK:
                    yield SSE_COMMENT_KEEPALIVE
                    continue
                item = raw
                for sse_line in bridge.process_item(item, builder, ctx):
                    yield sse_line
                if bridge.consume_persist_tick() and ctx.get("_assistant_db_id"):
                    try:
                        await _persist_assistant(
                            _assistant_content_snapshot(builder),
                            session_id,
                            current_user.user_id,
                            status="streaming",
                            assistant_message_id=ctx["_assistant_db_id"],
                        )
                    except Exception:
                        logger.exception(
                            f"assistant 流式检查点落库失败 session_id={session_id}"
                        )

            if ctx.get("text_buffer"):
                builder.append_text(ctx["text_buffer"])
                ctx["text_buffer"] = ""

            for sse_line in bridge.finalize():
                yield sse_line
                if bridge.consume_persist_tick() and ctx.get("_assistant_db_id"):
                    try:
                        await _persist_assistant(
                            _assistant_content_snapshot(builder),
                            session_id,
                            current_user.user_id,
                            status="streaming",
                            assistant_message_id=ctx["_assistant_db_id"],
                        )
                    except Exception:
                        logger.exception(
                            f"assistant 流式检查点落库失败 session_id={session_id}"
                        )

            if not task_cancelled:
                await _finalize_streaming_assistant(
                    builder=builder,
                    bridge=bridge,
                    ctx=ctx,
                    session_id=session_id,
                    user_id=current_user.user_id,
                    qa_type=IntentEnum.TEST_CASE_QA.value[0],
                )

            logger.info(
                f"exec_test_case_resume 流式正常结束 session_id={session_id} "
                f"assistant_message_id={bridge.assistant_message_id if bridge else ''} "
                f"finish_reason={bridge.last_finish_reason if bridge else ''}"
            )

            cls._current_builders.pop(session_id, None)

        except asyncio.CancelledError:
            task_cancelled = True
            logger.info(
                f"exec_test_case_resume 流式被取消(CancelledError) session_id={session_id} "
                f"user_id={current_user.user_id} assistant_db_id={(ctx or {}).get('_assistant_db_id')}"
            )
            aid = (ctx or {}).get("_assistant_db_id")
            tc_qa = IntentEnum.TEST_CASE_QA.value[0]
            if session_id in cls._current_builders:
                builder = cls._current_builders[session_id]
                if ctx.get("text_buffer") and builder:
                    builder.append_text(ctx["text_buffer"])
                    ctx["text_buffer"] = ""
                if builder and not builder.is_empty():
                    try:
                        await _persist_assistant(
                            builder.to_dict(),
                            session_id,
                            current_user.user_id,
                            status="partial",
                            extra={"qa_type": tc_qa},
                            assistant_message_id=aid,
                        )
                    except Exception:
                        logger.exception(
                            f"测试用例 resume 取消时 assistant 消息落库失败: session_id={session_id} user_id={current_user.user_id}"
                        )
                elif aid:
                    try:
                        await _persist_assistant(
                            {"version": 1, "parts": []},
                            session_id,
                            current_user.user_id,
                            status="partial",
                            extra={"qa_type": tc_qa},
                            assistant_message_id=aid,
                        )
                    except Exception:
                        logger.exception(
                            f"测试用例 resume 取消时空内容落库失败: session_id={session_id} user_id={current_user.user_id}"
                        )
            cls._current_builders.pop(session_id, None)
            raise

        except Exception as e:
            logging.exception(f"测试用例 resume 异常: {e}")
            aid = (ctx or {}).get("_assistant_db_id")
            tc_qa = IntentEnum.TEST_CASE_QA.value[0]
            persist_b = builder
            if persist_b is None and session_id in cls._current_builders:
                persist_b = cls._current_builders[session_id]
            if ctx.get("text_buffer") and persist_b is not None:
                persist_b.append_text(ctx["text_buffer"])
                ctx["text_buffer"] = ""
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
                b = builder if builder is not None else AssistantMessageBuilder(session_id=session_id)
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
            cls._current_builders.pop(session_id, None)

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
        """停止问答任务并保存当前累积的消息"""
        builder = cls._current_builders.get(session_id)
        logger.info(
            f"stop_chat 处理 session_id={session_id} qa_type={qa_type} user_id={current_user.user_id} "
            f"has_active_builder={builder is not None}"
        )
        aid = (getattr(builder, "message_id", None) or None) if builder else None
        if builder and (aid or not builder.is_empty()):
            try:
                await _persist_assistant(
                    _assistant_content_snapshot(builder),
                    session_id,
                    current_user.user_id,
                    status="partial",
                    extra={"qa_type": qa_type},
                    assistant_message_id=aid,
                )
            except Exception:
                logger.exception(
                    f"stop_chat 时 assistant 消息落库失败: session_id={session_id} user_id={current_user.user_id}"
                )

        # 2. 根据 qa_type 取消对应任务
        if qa_type == IntentEnum.COMMON_QA.value[0]:
            status = await common_agent.cancel_task(session_id)
            logger.info(f"stop_chat cancel_task COMMON_QA session_id={session_id} marked={status}")
            return status, "停止成功"
        elif qa_type == IntentEnum.FAULT_OPERATION_QA.value[0]:
            status = await fault_agent.cancel_task(session_id)
            logger.info(f"stop_chat cancel_task FAULT_OPERATION session_id={session_id} marked={status}")
            return status, "停止成功"
        elif qa_type == IntentEnum.TEST_CASE_QA.value[0]:
            status, msg = await case_coordinator.cancel_task(session_id)
            logger.info(f"stop_chat cancel_task TEST_CASE session_id={session_id} ok={status} msg={msg}")
            return status, msg
        elif qa_type == IntentEnum.DEEP_RESEARCH_QA.value[0]:
            status = await deep_research_agent.cancel_task(session_id)
            logger.info(f"stop_chat cancel_task DEEP_RESEARCH session_id={session_id} marked={status}")
            return status, "停止成功"

        # 3. 清理 builder
        cls._current_builders.pop(session_id, None)
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

