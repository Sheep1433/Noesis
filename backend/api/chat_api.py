"""
Chat API (v2.1)

实现 v2.1 设计的 API 接口：
1. 会话 API：GET/POST /api/chat/sessions、GET/DELETE /api/chat/sessions/{id}、PATCH /api/chat/sessions/{id}/title
2. 消息 API：GET /api/chat/sessions/{id}/messages、POST /api/chat/sessions/{id}/messages、GET /api/chat/messages/{id}
"""

import asyncio
import errno
import json
from typing import Optional
from pydantic import BaseModel, Field
from urllib.parse import quote

from fastapi import Body, Depends, APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from config.get_db import get_db
from schemas.login_vo import CurrentUser
from schemas.chat_vo import (
    CreateSessionRequest,
    UpdateSessionTitleRequest,
    ChatSessionResponse,
    ChatMessageResponse,
    SessionListResponse,
    MessageListResponse,
    SendMessageRequest,
    SendMessageResponse,
)
from services.chat_service import ChatService
from services.user_service import UserService
from services.qa_service import QaService
from utils.response_util import ResponseUtil
from utils.message_builder import UserMessageBuilder
from utils.log_util import logger
from constants.code_enum import IntentEnum
from schemas.qa_vo import QaStopRequest, TestCaseExportRequest, TestCaseResumeRequest


chat_router = APIRouter(prefix="/api/chat")

_EXPORT_FALLBACK_FILENAME = "test-cases-export.md"


def _attachment_content_disposition(filename: str) -> str:
    """Content-Disposition：filename 仅 ASCII，中文等非 ASCII 走 filename* UTF-8。"""
    encoded = quote(filename, safe="")
    if filename.isascii():
        return f'attachment; filename="{filename}"; filename*=UTF-8\'\'{encoded}'
    return f"attachment; filename={_EXPORT_FALLBACK_FILENAME}; filename*=UTF-8''{encoded}"


def _session_to_response(session) -> ChatSessionResponse:
    """将会话模型转换为响应格式"""
    return ChatSessionResponse(
        id=session.id,
        parent_id=session.parent_id,
        user_id=session.user_id,
        title=session.title,
        extra=session.extra,
        created_at=session.created_at,
        updated_at=session.updated_at,
        deleted_at=session.deleted_at
    )


def _message_to_response(message) -> ChatMessageResponse:
    """将消息模型转换为响应格式

    user 和 assistant 消息的 content 均为 multipart 格式。
    前端期望 content.parts 数组。
    """
    raw = message.content
    extra = message.extra or {}

    # 提取 parts 数组
    parts = []
    if isinstance(raw, dict):
        parts_data = raw.get("parts", [])
        if isinstance(parts_data, list):
            parts = parts_data
    elif isinstance(raw, (str, bytes)) and raw:
        try:
            content_str = raw.decode() if isinstance(raw, bytes) else raw
            parsed = json.loads(content_str)
            if isinstance(parsed, dict):
                parts_data = parsed.get("parts", [])
                if isinstance(parts_data, list):
                    parts = parts_data
        except Exception:
            pass

    return ChatMessageResponse(
        id=message.id,
        session_id=message.session_id,
        parent_id=message.parent_id,
        user_id=message.user_id,
        role=message.role,
        content={"parts": parts},
        extra=extra if extra else None,
        status=message.status,
        created_at=message.created_at
    )


# ============================================================================
# Session API
# ============================================================================

@chat_router.get("/sessions", summary="获取会话列表")
async def get_sessions(
    status: Optional[str] = None,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    获取当前用户的会话列表（过滤已删除）
    """
    sessions = await ChatService.get_user_sessions(
        user_id=str(current_user.user_id),
        status=status,
        db=db
    )

    session_responses = [_session_to_response(s) for s in sessions]

    return ResponseUtil.success(
        msg='获取会话列表成功',
        data=SessionListResponse(sessions=session_responses, total=len(session_responses)).model_dump()
    )


@chat_router.post("/sessions", summary="创建会话")
async def create_session(
    request: CreateSessionRequest,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    创建新会话（可指定 parent_id 创建子会话）
    """
    session = await ChatService.create_session(
        user_id=str(current_user.user_id),
        title=request.title,
        parent_id=request.parent_id,
        extra=request.extra,
        db=db
    )

    return ResponseUtil.success(
        msg='创建会话成功',
        data=_session_to_response(session).model_dump()
    )


@chat_router.get("/sessions/{session_id}", summary="获取会话详情")
async def get_session(
    session_id: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    获取会话详情
    """
    session = await ChatService.get_session_by_id(
        session_id=session_id,
        user_id=str(current_user.user_id),
        db=db
    )

    if not session:
        return ResponseUtil.not_found(msg='会话不存在')

    return ResponseUtil.success(
        msg='获取会话详情成功',
        data=_session_to_response(session).model_dump()
    )


@chat_router.delete("/sessions/{session_id}", summary="删除会话")
async def delete_session(
    session_id: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    删除会话（软删）
    """
    await ChatService.delete_session(
        session_id=session_id,
        user_id=str(current_user.user_id),
        db=db
    )

    return ResponseUtil.success(msg='删除会话成功')


@chat_router.patch("/sessions/{session_id}/title", summary="更新会话标题")
async def update_session_title(
    session_id: str,
    request: UpdateSessionTitleRequest,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    更新会话标题
    """
    session = await ChatService.update_session_title(
        session_id=session_id,
        user_id=str(current_user.user_id),
        title=request.title,
        db=db
    )

    return ResponseUtil.success(
        msg='更新会话标题成功',
        data=_session_to_response(session).model_dump()
    )


@chat_router.get("/sessions/{session_id}/children", summary="获取子会话列表")
async def get_child_sessions(
    session_id: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    获取子会话列表（subagent 场景）
    """
    sessions = await ChatService.get_child_sessions(
        parent_id=session_id,
        db=db
    )

    session_responses = [_session_to_response(s) for s in sessions]

    return ResponseUtil.success(
        msg='获取子会话列表成功',
        data=SessionListResponse(sessions=session_responses, total=len(session_responses)).model_dump()
    )


# ============================================================================
# Message API
# ============================================================================

@chat_router.get("/sessions/{session_id}/messages", summary="获取消息历史")
async def get_session_messages(
    session_id: str,
    limit: int = 100,
    before_id: str = None,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    获取会话消息历史（按 created_at 升序排序，支持分页）
    """
    messages = await ChatService.get_session_messages(
        session_id=session_id,
        db=db,
        limit=limit,
        before_id=before_id
    )

    message_responses = [_message_to_response(m) for m in messages]

    return ResponseUtil.success(
        msg='获取消息历史成功',
        data=MessageListResponse(messages=message_responses, total=len(message_responses)).model_dump()
    )


@chat_router.post("/sessions/{session_id}/messages", summary="发送消息")
async def send_message(
    session_id: str,
    request: SendMessageRequest,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    发送消息（创建用户消息）
    """
    # 构建消息内容
    builder = UserMessageBuilder(content=request.content)
    content = builder.serialize()

    message = await ChatService.save_message(
        session_id=session_id,
        user_id=str(current_user.user_id),
        role=request.role,
        content=content,
        extra=request.extra,
        parent_id=request.parent_id,
        status='completed',
        db=db
    )

    return ResponseUtil.success(
        msg='发送消息成功',
        data=SendMessageResponse(
            message_id=message.id,
            session_id=message.session_id,
            status=message.status
        ).model_dump()
    )


async def _event_generator(generator, session_id: str):
    """将 QaService 产出的 SSE 文本帧编码为 UTF-8 字节流。"""
    completed_normally = False
    try:
        async for sse_str in generator:
            try:
                yield sse_str.encode("utf-8")
            except (BrokenPipeError, ConnectionResetError):
                logger.info(
                    f"SSE 客户端已断开（连接重置），停止写入 session_id={session_id}"
                )
                return
            except OSError as exc:
                if getattr(exc, "errno", None) in (errno.EPIPE, errno.ECONNRESET):
                    logger.info(
                        f"SSE 客户端已断开 errno={exc.errno} session_id={session_id}"
                    )
                    return
                raise
        completed_normally = True
    except asyncio.CancelledError:
        logger.info(
            f"SSE StreamingResponse 消费被取消（多为客户端断开）session_id={session_id}"
        )
        raise
    except Exception:
        logger.exception(
            f"SSE StreamingResponse 消费异常 session_id={session_id}"
        )
        raise
    finally:
        if completed_normally:
            logger.info(f"SSE StreamingResponse 已完整发送 session_id={session_id}")



@chat_router.post("/sessions/stream", summary="流式发送消息并获取AI响应")
async def send_message_stream(
    request: SendMessageRequest,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    会话级流式问答接口：
    1. 创建用户消息记录
    2. 根据 qa_type 调用对应 Agent 流式返回
    3. 流结束后保存 assistant 消息

    返回 SSE 格式事件流
    """
    from schemas.qa_vo import QaQueryRequest, QaStopRequest

    session_id = request.session_id or ""
    qa_type = (request.extra or {}).get("qa_type", IntentEnum.COMMON_QA.value[0])
    file_dict = (request.extra or {}).get("file_dict")

    qa_req = QaQueryRequest(
        query=request.content,
        qa_type=qa_type,
        chat_id=session_id,
        file_dict=file_dict,
    )

    try:
        logger.info(
            f"流式问答开始 session_id={session_id or '(new)'} qa_type={qa_type} user_id={current_user.user_id}"
        )
        generator = QaService.exec_query(qa_req, current_user, db)
        return StreamingResponse(
            _event_generator(generator, session_id),
            media_type="text/event-stream"
        )
    except Exception as e:
        logger.exception(e)
        return StreamingResponse(
            iter([
                'event: error\ndata: {"type":"error","messageId":"","error":"服务异常"}\n\n',
                'event: finish\ndata: {"type":"finish","messageId":"","finishReason":"error","usage":{}}\n\n',
                'data: [DONE]\n\n',
            ]),
            media_type="text/event-stream",
        )


@chat_router.post("/sessions/{session_id}/test-case/resume", summary="测试用例：采纳测试点后继续生成")
async def resume_test_case_stream(
    session_id: str,
    request: TestCaseResumeRequest,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    在会话内继续执行 CaseCoordinator.resume_agent，流式返回用例内容（SSE）。
    须与同一会话上一次的 TEST_CASE_QA 首轮流式请求衔接。
    """
    if not request.selected_point_names:
        return ResponseUtil.failure(msg="请至少选择一个测试点")

    try:
        logger.info(
            f"测试用例 resume 流式开始 session_id={session_id} user_id={current_user.user_id} "
            f"points={len(request.selected_point_names or [])}"
        )
        generator = QaService.exec_test_case_resume(
            session_id=session_id,
            selected_point_names=request.selected_point_names,
            current_user=current_user,
            db=db,
        )
        return StreamingResponse(
            _event_generator(generator, session_id),
            media_type="text/event-stream",
        )
    except Exception as e:
        logger.exception(e)
        return StreamingResponse(
            iter([
                'event: error\ndata: {"type":"error","messageId":"","error":"服务异常"}\n\n',
                'event: finish\ndata: {"type":"finish","messageId":"","finishReason":"error","usage":{}}\n\n',
                'data: [DONE]\n\n',
            ]),
            media_type="text/event-stream",
        )


@chat_router.post("/sessions/{session_id}/test-case/export", summary="测试用例：导出 Markdown")
async def export_test_case_markdown(
    session_id: str,
    request: TestCaseExportRequest = Body(default_factory=TestCaseExportRequest),
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    将测试用例导出为 Markdown 文件下载。
    请求体可携带 test_cases；省略时从协调器读取本会话最近一次生成结果。
    """
    test_cases = None
    if request.test_cases:
        test_cases = [item.model_dump(exclude_none=True) for item in request.test_cases]

    try:
        markdown, filename = await QaService.export_test_case_markdown(
            session_id=session_id,
            current_user=current_user,
            db=db,
            test_cases=test_cases,
            query=request.query,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail="导出失败") from e

    return Response(
        content=markdown.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": _attachment_content_disposition(filename),
        },
    )


@chat_router.get("/messages/{message_id}", summary="获取消息详情")
async def get_message(
    message_id: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    获取单条消息详情
    """
    # 先获取消息（需要查询 session 来验证权限）
    # 这里简化处理，实际应该通过 ChatService 获取
    from sqlalchemy import select
    from model.chat_models import TChatMessage, TChatSession

    result = await db.execute(
        select(TChatMessage).where(TChatMessage.id == message_id)
    )
    message = result.scalar_one_or_none()

    if not message:
        return ResponseUtil.not_found(msg='消息不存在')

    # 验证用户权限
    session_result = await db.execute(
        select(TChatSession).where(TChatSession.id == message.session_id)
    )
    session = session_result.scalar_one_or_none()

    if not session or session.user_id != str(current_user.user_id):
        return ResponseUtil.not_found(msg='消息不存在')

    return ResponseUtil.success(
        msg='获取消息详情成功',
        data=_message_to_response(message).model_dump()
    )


@chat_router.post("/sessions/{session_id}/stop", summary="停止流式生成")
async def stop_stream(
    session_id: str,
    request: QaStopRequest,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """
    停止指定会话的流式生成任务
    """
    logger.info(
        f"停止流式生成请求 session_id={session_id} qa_type={request.qa_type} user_id={current_user.user_id}"
    )
    status, msg = await QaService.stop_chat(session_id, request.qa_type, current_user)
    logger.info(
        f"停止流式生成结果 session_id={session_id} ok={status} msg={msg}"
    )
    if status:
        return ResponseUtil.success(msg=msg)
    else:
        return ResponseUtil.failure(msg=msg)


class BatchDeleteRequest(BaseModel):
    session_ids: list[str] = Field(description="会话ID列表")


@chat_router.post("/sessions/batch-delete", summary="批量删除会话")
async def batch_delete_sessions(
    request: BatchDeleteRequest,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    批量删除会话（软删）
    """
    for session_id in request.session_ids:
        await ChatService.delete_session(session_id, current_user.user_id, db)
    return ResponseUtil.success(msg='删除成功')
