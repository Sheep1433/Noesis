"""
Chat API (v2.1)

实现 v2.1 设计的 API 接口：
1. 会话 API：GET/POST /api/chat/sessions、GET/DELETE /api/chat/sessions/{id}、PUT /api/chat/sessions/{id}/title
2. 消息 API：GET /api/chat/sessions/{id}/messages、POST /api/chat/sessions/{id}/messages、GET /api/chat/messages/{id}
"""

import asyncio
import errno
import json
from typing import Optional
from pydantic import BaseModel, Field
from urllib.parse import quote

from fastapi import Body, Depends, APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db

from noesis.schemas.login_vo import CurrentUser
from noesis.schemas.chat_vo import (
    CreateSessionRequest,
    EnsureSessionRequest,
    UpdateSessionTitleRequest,
    UpdateSessionMetaRequest,
    ChatSessionResponse,
    ChatMessageResponse,
    SessionListResponse,
    MessageListResponse,
    SendMessageRequest,
    SendMessageResponse,
    CreateRunRequest,
)
from noesis.schemas.session_context_vo import (
    WorkspaceFileContent,
    WorkspaceFileWriteRequest,
)
from noesis.services.session_context_service import SessionContextService
from noesis.services.chat_service import ChatService
from server.auth_dependencies import get_current_user, require_csrf
from noesis.services.qa import QaService
from noesis.services.run_service import RunService, run_manager
from noesis.storage.postgres.manager import pg_manager
from server.response import ResponseUtil
from noesis.chat.message_builder import (
    UserMessageBuilder,
    normalize_message_content_for_delivery,
)
from noesis.runtime.logging import logger
from noesis.config.code_enum import IntentEnum
from noesis.schemas.qa_vo import (
    HitlResumeRequest,
    TestCaseExportRequest,
    TestCaseResumeRequest,
)
from noesis.chat.delivery.sse import (
    SSE_COMMENT_KEEPALIVE,
    encode_sequenced_event,
    format_done,
    format_sse,
)
from noesis.chat.delivery.events import (
    RunAborted,
    RunCompleted,
    RunError,
    RunSnapshotReplaced,
    StreamDone,
)
from noesis.chat.runs import SlowSubscriber, SubscriptionLimitExceeded, TERMINAL_RUN_STATUSES


chat_router = APIRouter(prefix="/api/chat")

_EXPORT_FALLBACK_FILENAME = "test-cases-export.md"


async def _deny_foreign_session(
    session_id: str,
    current_user: CurrentUser,
    db: AsyncSession,
):
    """会话已存在且不属于当前用户时返回 404 响应，否则返回 None。"""
    if not session_id:
        return None
    if await ChatService.is_session_owned_by_other(
        session_id,
        str(current_user.user_id),
        db,
    ):
        return ResponseUtil.not_found(msg='会话不存在')
    return None


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
        deleted_at=session.deleted_at,
        pinned=bool(getattr(session, 'pinned', False)),
        archived=bool(getattr(session, 'archived', False)),
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

    content = {"parts": parts}
    if message.role == "assistant":
        content = normalize_message_content_for_delivery(content)

    return ChatMessageResponse(
        id=message.id,
        session_id=message.session_id,
        parent_id=message.parent_id,
        user_id=message.user_id,
        role=message.role,
        content=content,
        extra=extra if extra else None,
        status=message.status,
        message_sequence=message.message_sequence,
        created_at=message.created_at
    )


# ============================================================================
# Session API
# ============================================================================

@chat_router.get("/sessions", summary="获取会话列表")
async def get_sessions(
    status: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user),
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
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    创建新会话（可指定 parent_id 创建子会话）
    """
    if request.parent_id:
        parent = await ChatService.get_session_by_id(
            session_id=request.parent_id,
            user_id=str(current_user.user_id),
            db=db,
        )
        if not parent:
            return ResponseUtil.not_found(msg='父会话不存在')

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


class BatchDeleteRequest(BaseModel):
    session_ids: list[str] = Field(description="会话ID列表")


@chat_router.post("/sessions/batch-delete", summary="批量删除会话")
async def batch_delete_sessions(
    request: BatchDeleteRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    批量删除会话（软删）
    """
    deleted = await ChatService.batch_delete_sessions(
        session_ids=request.session_ids,
        user_id=str(current_user.user_id),
        db=db,
    )
    return ResponseUtil.success(msg=f'已删除 {deleted} 个会话')


@chat_router.put("/sessions/{session_id}/ensure", summary="幂等物化会话")
async def ensure_session(
    session_id: str,
    request: EnsureSessionRequest = Body(default=EnsureSessionRequest()),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    按 client 提供的 session_id 获取或创建会话，供发送前 upload 附件使用。
    """
    denied = await _deny_foreign_session(session_id, current_user, db)
    if denied:
        return denied

    session = await ChatService.get_or_create_session(
        user_id=str(current_user.user_id),
        session_id=session_id,
        title=request.title,
        extra=request.extra,
        db=db,
    )
    if request.extra:
        await ChatService.merge_session_extra(
            session_id,
            str(current_user.user_id),
            request.extra,
            db=db,
        )
        refreshed = await ChatService.get_session_by_id(
            session_id,
            user_id=str(current_user.user_id),
            db=db,
        )
        if refreshed is not None:
            session = refreshed
    return ResponseUtil.success(
        msg='会话已就绪',
        data=_session_to_response(session).model_dump(),
    )


@chat_router.get("/sessions/{session_id}", summary="获取会话详情")
async def get_session(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
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
    current_user: CurrentUser = Depends(get_current_user),
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


@chat_router.put("/sessions/{session_id}/title", summary="更新会话标题")
async def update_session_title(
    session_id: str,
    request: UpdateSessionTitleRequest,
    current_user: CurrentUser = Depends(get_current_user),
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


@chat_router.put("/sessions/{session_id}/meta", summary="更新会话置顶/归档状态")
async def update_session_meta(
    session_id: str,
    request: UpdateSessionMetaRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    更新会话置顶 / 归档状态；pinned / archived 为 null 表示不修改。
    """
    session = await ChatService.update_session_meta(
        session_id=session_id,
        user_id=str(current_user.user_id),
        pinned=request.pinned,
        archived=request.archived,
        db=db
    )

    return ResponseUtil.success(
        msg='更新会话状态成功',
        data=_session_to_response(session).model_dump()
    )


@chat_router.get("/sessions/{session_id}/children", summary="获取子会话列表")
async def get_child_sessions(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    获取子会话列表（subagent 场景）
    """
    session = await ChatService.get_session_by_id(
        session_id=session_id,
        user_id=str(current_user.user_id),
        db=db,
    )
    if not session:
        return ResponseUtil.not_found(msg='会话不存在')

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
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    获取会话消息历史（按 created_at 升序排序，支持分页）
    """
    session = await ChatService.get_session_by_id(
        session_id=session_id,
        user_id=str(current_user.user_id),
        db=db,
    )
    if not session:
        return ResponseUtil.not_found(msg='会话不存在')

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


@chat_router.get(
    "/sessions/{session_id}/context",
    summary="获取会话上下文（工作区产物 + 附件）",
)
async def get_session_context(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        payload = await SessionContextService.get_context(
            session_id=session_id,
            user_id=str(current_user.user_id),
            db=db,
        )
        return ResponseUtil.success(data=payload.model_dump())
    except HTTPException as exc:
        if exc.status_code == 404:
            return ResponseUtil.not_found(msg=str(exc.detail))
        raise


@chat_router.get(
    "/sessions/{session_id}/workspace/file",
    summary="读取会话工作区文本文件",
)
async def get_session_workspace_file(
    session_id: str,
    path: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        rel, content = await SessionContextService.read_workspace_file(
            session_id=session_id,
            user_id=str(current_user.user_id),
            rel_path=path,
            db=db,
        )
        return ResponseUtil.success(
            data=WorkspaceFileContent(path=rel, content=content).model_dump(),
        )
    except HTTPException as exc:
        if exc.status_code == 404:
            return ResponseUtil.not_found(msg=str(exc.detail))
        if exc.status_code == 400:
            return ResponseUtil.failure(msg=str(exc.detail))
        raise


@chat_router.get(
    "/sessions/{session_id}/workspace/archive",
    summary="下载工作区目录或文件",
)
async def get_session_workspace_archive(
    session_id: str,
    path: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        filename, data, media_type = await SessionContextService.download_workspace_path(
            session_id=session_id,
            user_id=str(current_user.user_id),
            rel_path=path,
            db=db,
        )
        encoded_name = quote(filename)
        return Response(
            content=data,
            media_type=media_type,
            headers={
                'Content-Disposition': f"attachment; filename*=UTF-8''{encoded_name}",
            },
        )
    except HTTPException as exc:
        if exc.status_code == 404:
            return ResponseUtil.not_found(msg=str(exc.detail))
        if exc.status_code == 400:
            return ResponseUtil.failure(msg=str(exc.detail))
        raise


@chat_router.put(
    "/sessions/{session_id}/workspace/file",
    summary="保存会话工作区文本文件",
)
async def put_session_workspace_file(
    session_id: str,
    request: WorkspaceFileWriteRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        rel, content = await SessionContextService.write_workspace_file(
            session_id=session_id,
            user_id=str(current_user.user_id),
            rel_path=request.path,
            content=request.content,
            db=db,
        )
        return ResponseUtil.success(
            data=WorkspaceFileContent(path=rel, content=content).model_dump(),
        )
    except HTTPException as exc:
        if exc.status_code == 404:
            return ResponseUtil.not_found(msg=str(exc.detail))
        if exc.status_code == 400:
            return ResponseUtil.failure(msg=str(exc.detail))
        raise


@chat_router.post("/sessions/{session_id}/messages", summary="发送消息")
async def send_message(
    session_id: str,
    request: SendMessageRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    发送消息（创建用户消息）
    """
    session = await ChatService.get_session_by_id(
        session_id=session_id,
        user_id=str(current_user.user_id),
        db=db,
    )
    if not session:
        return ResponseUtil.not_found(msg='会话不存在')

    # 构建消息内容
    builder = UserMessageBuilder(content=request.content)
    content = builder.serialize()

    message = await ChatService.save_message(
        session_id=session_id,
        user_id=str(current_user.user_id),
        role='user',
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
    client_disconnected = False
    try:
        async for sse_str in generator:
            try:
                yield sse_str.encode("utf-8")
            except (BrokenPipeError, ConnectionResetError):
                logger.info(
                    f"SSE 客户端已断开（连接重置），停止写入 session_id={session_id}"
                )
                client_disconnected = True
                return
            except OSError as exc:
                if getattr(exc, "errno", None) in (errno.EPIPE, errno.ECONNRESET):
                    logger.info(
                        f"SSE 客户端已断开 errno={exc.errno} session_id={session_id}"
                    )
                    client_disconnected = True
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
        if client_disconnected:
            await generator.aclose()
        if completed_normally:
            logger.info(f"SSE StreamingResponse 已完整发送 session_id={session_id}")



@chat_router.get("/commands", summary="列出可用斜杠命令")
async def list_commands(
    current_user: CurrentUser = Depends(get_current_user),
):
    """返回控制命令（name + description）。skill 命令由 /skills fs-tree 提供。"""
    from noesis.chat.commands.registry import list_command_descriptions

    items = [{"name": name, "description": desc} for name, desc in list_command_descriptions()]
    return ResponseUtil.success(msg="获取命令列表成功", data=items)


@chat_router.post("/runs", summary="创建 Agent 任务")
async def create_run(
    request: CreateRunRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if pg_manager.advisory_lock_ready is False:
        return ResponseUtil.service_unavailable(
            msg="任务运行实例暂时不可用，请稍后重试",
            data={"error_code": "RUN_OWNER_UNAVAILABLE"},
        )

    # 统一命令层：进 Agent 前（建 run 前）先 dispatch。
    # 命中则 ephemeral 回复 —— 不建 run、不落库（user 消息也不持久化）。
    from noesis.chat.commands.registry import dispatch as dispatch_command
    from noesis.chat.delivery.channels import InboundMessage

    inbound = InboundMessage(
        channel_type="web",
        external_chat_id=request.session_id,
        text=request.content,
        user_id=str(current_user.user_id),
    )
    cmd_result = await dispatch_command(inbound)
    if cmd_result.handled and not cmd_result.rewrite_request:
        return ResponseUtil.success(
            msg="命令已处理",
            data={
                "command_reply": cmd_result.text,
                "session_id": request.session_id,
            },
        )
    # D 类 skill 快捷命令：改写 query + enabled_skills，走正常 Agent run。
    if cmd_result.handled and cmd_result.rewrite_request:
        rw = cmd_result.rewrite_request
        extra = dict(request.extra or {})
        extra["enabled_skills"] = rw.enabled_skills
        request = CreateRunRequest(
            session_id=request.session_id,
            content=rw.query,
            client_request_id=request.client_request_id,
            extra=extra,
        )

    run = await RunService.create(request, current_user, db)
    session = await ChatService.get_session_by_id(
        run.session_id, str(current_user.user_id), db
    )
    return ResponseUtil.success(
        msg="任务已创建",
        data={
            "run_id": run.id,
            "assistant_message_id": run.assistant_message_id,
            "session_id": run.session_id,
            "status": run.status,
            "session_title": session.title,
        },
    )


@chat_router.get("/runs/{run_id}", summary="获取 Agent 任务状态")
async def get_run(
    run_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    snapshot = await RunService.get(run_id, str(current_user.user_id), db)
    return ResponseUtil.success(msg="获取任务成功", data=snapshot.to_dict())


@chat_router.get("/sessions/{session_id}/active-run", summary="查询会话活跃任务")
async def get_active_run(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """返回当前 session 的 active Run snapshot 或 data=null。

    新 Tab、刷新页和断线恢复使用此端点从服务端发现 active Run，
    不依赖其它 Tab 的 sessionStorage。返回结构与 GET /runs/{run_id} 一致。
    """
    snapshot = await RunService.get_active_run(
        session_id, str(current_user.user_id), db
    )
    if snapshot is None:
        return ResponseUtil.success(msg="无活跃任务", dict_content={"data": None})
    return ResponseUtil.success(msg="获取活跃任务成功", data=snapshot.to_dict())


@chat_router.get("/runs/{run_id}/stream", summary="订阅 Agent 任务事件")
async def stream_run(
    run_id: str,
    after_sequence: int = 0,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    snapshot = await RunService.get(run_id, str(current_user.user_id), db)
    try:
        subscription = await RunService.subscribe(
            run_id, str(current_user.user_id), max(0, after_sequence), db
        )
    except SubscriptionLimitExceeded:
        return ResponseUtil.too_many_requests(
            msg="订阅连接数超限，请关闭其它标签页后重试",
            data={"error_code": "SSE_SUBSCRIPTION_LIMIT"},
        )

    # Owner 不可达：非终态 Run 在开流前返回 503，不创建第二 producer。
    # 终态 Run 仍返回 snapshot + [DONE]（客户端可看到权威终态）。
    if subscription is None and not snapshot.is_terminal:
        return ResponseUtil.service_unavailable(
            msg="任务运行实例暂时不可达，请稍后重试",
            data={
                "run_id": run_id,
                "error_code": "RUN_OWNER_UNAVAILABLE",
                "status": snapshot.status.value,
                "sequence": snapshot.sequence,
            },
        )

    async def event_stream():
        if subscription is None:
            # 终态 + owner 不可达：返回 DB 权威终态 snapshot + [DONE]
            yield format_sse("run-snapshot", {"type": "run-snapshot", **snapshot.to_dict()})
            yield format_done()
            return

        try:
            if after_sequence > 0 and subscription.replay:
                for envelope in subscription.replay:
                    for line in encode_sequenced_event(envelope):
                        yield line
            else:
                yield format_sse(
                    "run-snapshot",
                    {"type": "run-snapshot", **subscription.snapshot.to_dict()},
                )
            if subscription.snapshot.is_terminal:
                yield format_done()
                return

            while True:
                try:
                    item = await asyncio.wait_for(subscription.queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield SSE_COMMENT_KEEPALIVE
                    continue
                if isinstance(item, SlowSubscriber):
                    return
                for line in encode_sequenced_event(item):
                    yield line
                run_manager.record_event_delivered(item)
                if isinstance(
                    item.event,
                    (RunCompleted, RunAborted, RunError, RunSnapshotReplaced),
                ):
                    yield format_done()
                    return
                if isinstance(item.event, StreamDone):
                    return
        finally:
            try:
                await run_manager.unsubscribe(run_id, subscription.queue)
            except KeyError:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@chat_router.post("/runs/{run_id}/stop", summary="停止 Agent 任务")
async def stop_run(
    run_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # TEMP-DEBUG: 排查前端“停止请求失败”的真实原因
    try:
        snapshot = await RunService.stop(run_id, str(current_user.user_id), db)
        logger.info(
            "[TEMP-DEBUG] stop_run 成功 run_id={} user_id={} status={}",
            run_id, current_user.user_id, snapshot.status.value,
        )
        return ResponseUtil.success(
            msg="已停止生成" if snapshot.status.value == "partial" else "任务已结束",
            data=snapshot.to_dict(),
        )
    except Exception as e:
        logger.warning(
            "[TEMP-DEBUG] stop_run 失败 run_id={} user_id={} type={} msg={}",
            run_id, current_user.user_id, type(e).__name__, str(e),
        )
        raise


@chat_router.post("/runs/{run_id}/test-case/resume", summary="采纳测试点后继续 Agent 任务")
async def resume_test_case_run(
    run_id: str,
    request: TestCaseResumeRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not request.selected_point_names:
        return ResponseUtil.failure(msg="请至少选择一个测试点")
    snapshot = await RunService.resume_test_case(run_id, request, current_user, db)
    return ResponseUtil.success(msg="任务已继续", data=snapshot.to_dict())


@chat_router.post("/runs/{run_id}/hitl/resume", summary="审批后继续 Agent 任务")
async def resume_hitl_run(
    run_id: str,
    request: HitlResumeRequest,
    http_request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_csrf(http_request)
    snapshot = await RunService.resume_hitl(run_id, request, current_user, db)
    return ResponseUtil.success(msg="任务已继续", data=snapshot.to_dict())


@chat_router.post("/sessions/{session_id}/test-case/export", summary="测试用例：导出 Markdown")
async def export_test_case_markdown(
    session_id: str,
    request: TestCaseExportRequest = Body(default_factory=TestCaseExportRequest),
    current_user: CurrentUser = Depends(get_current_user),
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
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    获取单条消息详情
    """
    # 先获取消息（需要查询 session 来验证权限）
    # 这里简化处理，实际应该通过 ChatService 获取
    from sqlalchemy import select
    from noesis.storage.postgres.models.chat import TChatMessage, TChatSession

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
