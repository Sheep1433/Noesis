"""聊天会话附件 API。"""

import mimetypes

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from config.get_db import get_db
from exceptions.exception import ServiceWarning
from schemas.chat_attachment_vo import AttachmentListResponse
from schemas.login_vo import CurrentUser
from services.chat_attachment_service import ChatAttachmentService
from services.user_service import UserService
from utils.response_util import ResponseUtil

chat_attachment_router = APIRouter(prefix="/api/chat")


@chat_attachment_router.post(
    "/sessions/{session_id}/attachments",
    summary="上传会话附件",
)
async def upload_attachment(
    session_id: str,
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    try:
        result = await ChatAttachmentService.upload(
            session_id=session_id,
            user_id=str(current_user.user_id),
            filename=file.filename or "file",
            content=content,
            mime_type=file.content_type,
            db=db,
        )
        return ResponseUtil.success(msg="上传成功", data=result.model_dump())
    except ServiceWarning as exc:
        return ResponseUtil.failure(msg=exc.message or "上传失败")
    except HTTPException as exc:
        if exc.status_code == 404:
            return ResponseUtil.not_found(msg=str(exc.detail))
        if exc.status_code == 422:
            return JSONResponse(
                status_code=422,
                content={
                    "code": 422,
                    "msg": str(exc.detail),
                    "success": False,
                },
            )
        raise


@chat_attachment_router.get(
    "/sessions/{session_id}/attachments",
    summary="列出会话附件",
)
async def list_attachments(
    session_id: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        items = await ChatAttachmentService.list_attachments(
            session_id=session_id,
            user_id=str(current_user.user_id),
            db=db,
        )
        payload = AttachmentListResponse(
            attachments=items,
            total=len(items),
        ).model_dump()
        return ResponseUtil.success(msg="获取成功", data=payload)
    except HTTPException as exc:
        if exc.status_code == 404:
            return ResponseUtil.not_found(msg=str(exc.detail))
        raise


@chat_attachment_router.delete(
    "/sessions/{session_id}/attachments/{attachment_id}",
    summary="删除会话附件",
)
async def delete_attachment(
    session_id: str,
    attachment_id: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await ChatAttachmentService.delete_attachment(
            session_id=session_id,
            attachment_id=attachment_id,
            user_id=str(current_user.user_id),
            db=db,
        )
        return ResponseUtil.success(msg="删除成功")
    except HTTPException as exc:
        if exc.status_code == 404:
            return ResponseUtil.not_found(msg=str(exc.detail))
        raise


@chat_attachment_router.get(
    "/sessions/{session_id}/artifacts/{relative_path:path}",
    summary="预览会话附件文件",
)
async def get_artifact(
    session_id: str,
    relative_path: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        path, mime = await ChatAttachmentService.get_artifact_file(
            session_id=session_id,
            user_id=str(current_user.user_id),
            relative_path=relative_path,
            db=db,
        )
        return FileResponse(
            path=str(path),
            media_type=mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            filename=path.name,
        )
    except HTTPException as exc:
        if exc.status_code == 404:
            return ResponseUtil.not_found(msg=str(exc.detail))
        if exc.status_code in (400, 403):
            return ResponseUtil.failure(msg=str(exc.detail))
        raise
