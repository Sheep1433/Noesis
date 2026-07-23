"""用户记忆 / 定时任务 / 通讯通道 API（挂在 /api/user）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from common.http.response import ResponseUtil
from config.get_db import get_db
from schemas.login_vo import CurrentUser
from services.messaging_channel_service import MessagingChannelService
from services.scheduled_task_service import ScheduledTaskService
from services.user_memory_service import UserMemoryService
from services.user_service import UserService

user_settings_router = APIRouter(prefix="/api/user", tags=["用户设置"])


class MemoryWriteBody(BaseModel):
    content: str = Field(..., description="Markdown 正文")


class ScheduledTaskCreateBody(BaseModel):
    name: str
    cron_expr: str
    timezone: str = "Asia/Shanghai"
    enabled: bool = True
    qa_type: str = "SUPER_AGENT_QA"
    prompt: str = ""
    session_binding: str = "none"
    delivery: str = "none"


class ScheduledTaskUpdateBody(BaseModel):
    name: Optional[str] = None
    cron_expr: Optional[str] = None
    timezone: Optional[str] = None
    enabled: Optional[bool] = None
    qa_type: Optional[str] = None
    prompt: Optional[str] = None
    session_binding: Optional[str] = None
    delivery: Optional[str] = None


class ChannelUpsertBody(BaseModel):
    type: str = "telegram"
    enabled: bool = True
    display_name: str = ""
    bot_token: Optional[str] = None
    pairing_chat_id: Optional[str] = None
    default_qa_type: str = "SUPER_AGENT_QA"
    default_session_id: Optional[str] = None


# ----- memory -----


@user_settings_router.get("/memory/{file_name}")
async def get_user_memory_file(
    file_name: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    try:
        data = UserMemoryService.read_file(current_user.user_id, file_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ResponseUtil.success(data=data)


@user_settings_router.put("/memory/{file_name}")
async def put_user_memory_file(
    file_name: str,
    body: MemoryWriteBody,
    request: Request,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    await UserService.require_csrf(request)
    try:
        data = UserMemoryService.write_file(current_user.user_id, file_name, body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ResponseUtil.success(msg="已保存", data=data)


# ----- scheduled tasks -----


@user_settings_router.get("/scheduled-tasks")
async def list_scheduled_tasks(
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items = await ScheduledTaskService.list_tasks(db, current_user.user_id)
    return ResponseUtil.success(data={"tasks": items})


@user_settings_router.post("/scheduled-tasks")
async def create_scheduled_task(
    body: ScheduledTaskCreateBody,
    request: Request,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await UserService.require_csrf(request)
    try:
        item = await ScheduledTaskService.create_task(
            db, current_user.user_id, body.model_dump()
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ResponseUtil.success(msg="已创建", data=item)


@user_settings_router.get("/scheduled-tasks/{task_id}")
async def get_scheduled_task(
    task_id: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    item = await ScheduledTaskService.get_task(db, current_user.user_id, task_id)
    if item is None:
        return ResponseUtil.not_found(msg="任务不存在")
    return ResponseUtil.success(data=item)


@user_settings_router.put("/scheduled-tasks/{task_id}")
async def update_scheduled_task(
    task_id: str,
    body: ScheduledTaskUpdateBody,
    request: Request,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await UserService.require_csrf(request)
    try:
        item = await ScheduledTaskService.update_task(
            db,
            current_user.user_id,
            task_id,
            {k: v for k, v in body.model_dump().items() if v is not None},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if item is None:
        return ResponseUtil.not_found(msg="任务不存在")
    return ResponseUtil.success(msg="已更新", data=item)


@user_settings_router.delete("/scheduled-tasks/{task_id}")
async def delete_scheduled_task(
    task_id: str,
    request: Request,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await UserService.require_csrf(request)
    ok = await ScheduledTaskService.delete_task(db, current_user.user_id, task_id)
    if not ok:
        return ResponseUtil.not_found(msg="任务不存在")
    return ResponseUtil.success(msg="已删除")


@user_settings_router.post("/scheduled-tasks/{task_id}/enable")
async def enable_scheduled_task(
    task_id: str,
    request: Request,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await UserService.require_csrf(request)
    item = await ScheduledTaskService.set_enabled(db, current_user.user_id, task_id, True)
    if item is None:
        return ResponseUtil.not_found(msg="任务不存在")
    return ResponseUtil.success(msg="已启用", data=item)


@user_settings_router.post("/scheduled-tasks/{task_id}/disable")
async def disable_scheduled_task(
    task_id: str,
    request: Request,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await UserService.require_csrf(request)
    item = await ScheduledTaskService.set_enabled(db, current_user.user_id, task_id, False)
    if item is None:
        return ResponseUtil.not_found(msg="任务不存在")
    return ResponseUtil.success(msg="已停用", data=item)


@user_settings_router.post("/scheduled-tasks/{task_id}/run")
async def run_scheduled_task_once(
    task_id: str,
    request: Request,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await UserService.require_csrf(request)
    try:
        item = await ScheduledTaskService.run_once(db, current_user.user_id, task_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if item is None:
        return ResponseUtil.not_found(msg="任务不存在")
    return ResponseUtil.success(msg="已触发", data=item)


# ----- channels -----


@user_settings_router.get("/channels")
async def list_channels(
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    items = MessagingChannelService.list_channels(current_user.user_id)
    return ResponseUtil.success(data={"channels": items})


@user_settings_router.post("/channels")
async def create_channel(
    body: ChannelUpsertBody,
    request: Request,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    await UserService.require_csrf(request)
    try:
        item = MessagingChannelService.create_channel(
            current_user.user_id, body.model_dump()
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ResponseUtil.success(msg="已创建", data=item)


@user_settings_router.put("/channels/{channel_id}")
async def update_channel(
    channel_id: str,
    body: ChannelUpsertBody,
    request: Request,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    await UserService.require_csrf(request)
    try:
        item = MessagingChannelService.update_channel(
            current_user.user_id, channel_id, body.model_dump()
        )
    except KeyError:
        return ResponseUtil.not_found(msg="通道不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ResponseUtil.success(msg="已更新", data=item)


@user_settings_router.delete("/channels/{channel_id}")
async def delete_channel(
    channel_id: str,
    request: Request,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    await UserService.require_csrf(request)
    try:
        MessagingChannelService.delete_channel(current_user.user_id, channel_id)
    except KeyError:
        return ResponseUtil.not_found(msg="通道不存在")
    return ResponseUtil.success(msg="已删除")
