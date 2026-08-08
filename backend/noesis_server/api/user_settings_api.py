"""用户记忆 / 定时任务 / 通讯通道 API（挂在 /api/user）。"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from noesis_server.common.http.response import ResponseUtil
from noesis_server.infrastructure.database.dependency import get_db
from noesis.schemas.login_vo import CurrentUser
from noesis.services.messaging_channel_service import MessagingChannelService
from noesis.services.memory_dream_service import MemoryDreamService
from noesis.services.scheduled_task_service import ScheduledTaskService
from noesis.services.scheduled_task_service import compute_next_run_ms, cron_summary
from noesis.services.user_memory_service import UserMemoryService
from noesis.services.user_service import UserService
from noesis.services.settings_service import SettingsService

user_settings_router = APIRouter(prefix="/api/user", tags=["用户设置"])


class MemoryWriteBody(BaseModel):
    content: str = Field(..., description="Markdown 正文")


class MemoryDreamBody(BaseModel):
    date: str = Field(default_factory=lambda: date.today().isoformat())
    timezone: str = "Asia/Shanghai"


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
    model_config = ConfigDict(extra="forbid")

    type: str = "telegram"
    enabled: bool = True
    display_name: str = ""
    bot_token: Optional[str] = None
    bot_token_action: Literal["keep", "replace", "clear"] = "keep"
    pairing_chat_id: Optional[str] = None
    pairing_user_id: Optional[str] = None
    default_qa_type: str = "SUPER_AGENT_QA"
    default_session_id: Optional[str] = None
    session_strategy: Literal["persistent", "new_per_message"] = "persistent"
    delivery_preference: Literal["reply", "silent"] = "reply"


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


@user_settings_router.post("/memory/dream")
async def run_memory_dream(
    body: MemoryDreamBody,
    request: Request,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await UserService.require_csrf(request)
    try:
        data = await MemoryDreamService.run(db, user_id=current_user.user_id, target_date=body.date, timezone_name=body.timezone)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseUtil.success(msg="记忆整理完成", data=data)


@user_settings_router.get("/memory/daily/list")
async def list_daily_memory(current_user: CurrentUser = Depends(UserService.get_current_user)):
    return ResponseUtil.success(data={"items": UserMemoryService.list_daily(current_user.user_id)})


@user_settings_router.get("/memory/daily/search")
async def search_daily_memory(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=50),
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    try:
        items = UserMemoryService.search_daily(current_user.user_id, q, limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseUtil.success(data={"items": items})


@user_settings_router.get("/memory/daily/entries/search")
async def search_memory_entries(
    q: str = Query(..., min_length=1, max_length=100),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=50),
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    try:
        items = UserMemoryService.search_entries(current_user.user_id, q, date_from=date_from, date_to=date_to, category=category, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseUtil.success(data={"items": items})


@user_settings_router.get("/memory/daily/source")
async def get_memory_source(
    session_id: str = Query(...),
    message_id: str = Query(...),
    context_messages: int = Query(1, ge=0, le=3),
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = await MemoryDreamService.get_source(db, user_id=current_user.user_id, session_id=session_id, message_id=message_id, context_messages=context_messages)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ResponseUtil.success(data=data)


@user_settings_router.get("/context/preview")
async def preview_agent_context(
    profile: str = Query("super_agent"),
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    from noesis.context import ContextResolver
    try:
        data = ContextResolver.resolve(current_user.user_id, profile).public_view()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="不支持的 Agent 类型") from exc
    return ResponseUtil.success(data=data)


# ----- scheduled tasks -----


@user_settings_router.get("/scheduled-tasks/preview")
async def preview_scheduled_task(
    cron_expr: str = Query(...),
    timezone: str = Query("Asia/Shanghai"),
    _current_user: CurrentUser = Depends(UserService.get_current_user),
):
    try:
        return ResponseUtil.success(data={
            "summary": cron_summary(cron_expr, timezone),
            "next_run_at": compute_next_run_ms(cron_expr, timezone),
            "timezone": timezone,
        })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    await SettingsService.append_audit(db, user_id=current_user.user_id, action="automation.create", setting_domain="automation", target_id=item["id"], summary={"fields": ["name", "cron_expr", "timezone", "enabled", "qa_type", "session_binding", "delivery"]})
    await db.commit()
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
    await SettingsService.append_audit(db, user_id=current_user.user_id, action="automation.update", setting_domain="automation", target_id=task_id, summary={"fields": list({k for k, v in body.model_dump().items() if v is not None})})
    await db.commit()
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
    await SettingsService.append_audit(db, user_id=current_user.user_id, action="automation.delete", setting_domain="automation", target_id=task_id)
    await db.commit()
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
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    await UserService.require_csrf(request)
    try:
        item = await ScheduledTaskService.run_once(db, current_user.user_id, task_id, idempotency_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if item is None:
        return ResponseUtil.not_found(msg="任务不存在")
    return ResponseUtil.success(msg="已触发", data=item)


@user_settings_router.get("/scheduled-tasks/{task_id}/runs")
async def list_scheduled_task_runs(
    task_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await ScheduledTaskService.list_runs(db, current_user.user_id, task_id, page, page_size)
    if result.pop("not_found", False):
        return ResponseUtil.not_found(msg="任务不存在")
    return ResponseUtil.success(data=result)


@user_settings_router.get("/scheduled-task-runs/{run_id}")
async def get_scheduled_task_run(
    run_id: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    run = await ScheduledTaskService.get_run(db, current_user.user_id, run_id)
    if run is None:
        return ResponseUtil.not_found(msg="运行记录不存在")
    from noesis.services.scheduled_task_service import _run_to_dict
    return ResponseUtil.success(data=_run_to_dict(run))


@user_settings_router.post("/scheduled-task-runs/{run_id}/retry")
async def retry_scheduled_task_run(
    run_id: str,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await UserService.require_csrf(request)
    try:
        run = await ScheduledTaskService.retry_run(db, current_user.user_id, run_id, idempotency_key)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if run is None:
        return ResponseUtil.not_found(msg="运行记录不存在")
    return ResponseUtil.success(msg="已创建重试运行", data=run)


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
    db: AsyncSession = Depends(get_db),
):
    await UserService.require_csrf(request)
    try:
        payload = body.model_dump()
        if body.bot_token:
            payload["bot_token_action"] = "replace"
        await _validate_channel_session(db, current_user.user_id, body.session_strategy, body.default_session_id)
        item = MessagingChannelService.create_channel(
            current_user.user_id, payload
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await SettingsService.append_audit(db, user_id=current_user.user_id, action="channel.create", setting_domain="channel", target_id=item["channel_id"], summary={"fields": ["type", "enabled", "display_name", "pairing", "routing"], "secret_action": payload["bot_token_action"] if body.type == "telegram" else "none"})
    await db.commit()
    return ResponseUtil.success(msg="已创建", data=item)


@user_settings_router.put("/channels/{channel_id}")
async def update_channel(
    channel_id: str,
    body: ChannelUpsertBody,
    request: Request,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await UserService.require_csrf(request)
    try:
        await _validate_channel_session(db, current_user.user_id, body.session_strategy, body.default_session_id)
        item = MessagingChannelService.update_channel(
            current_user.user_id, channel_id, body.model_dump()
        )
    except KeyError:
        return ResponseUtil.not_found(msg="通道不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await SettingsService.append_audit(db, user_id=current_user.user_id, action="channel.update", setting_domain="channel", target_id=channel_id, summary={"fields": ["type", "enabled", "display_name", "pairing", "routing"], "secret_action": body.bot_token_action if body.type == "telegram" else "none"})
    await db.commit()
    return ResponseUtil.success(msg="已更新", data=item)


@user_settings_router.delete("/channels/{channel_id}")
async def delete_channel(
    channel_id: str,
    request: Request,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await UserService.require_csrf(request)
    try:
        MessagingChannelService.delete_channel(current_user.user_id, channel_id)
    except KeyError:
        return ResponseUtil.not_found(msg="通道不存在")
    await SettingsService.append_audit(db, user_id=current_user.user_id, action="channel.delete", setting_domain="channel", target_id=channel_id)
    await db.commit()
    return ResponseUtil.success(msg="已删除")


async def _validate_channel_session(db: AsyncSession, user_id: int, strategy: str, session_id: str | None) -> None:
    if strategy != "persistent" or not session_id:
        return
    from noesis.services.chat_service import ChatService
    if await ChatService.get_session_by_id(session_id, user_id=user_id, db=db) is None:
        raise HTTPException(status_code=400, detail="所选会话不存在")


@user_settings_router.post("/channels/{channel_id}/test-connection")
async def test_channel_connection(channel_id: str, request: Request, current_user: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    from noesis.services.channel_operations_service import ChannelOperationsService
    await UserService.require_csrf(request)
    try:
        result = await ChannelOperationsService.test_connection(current_user.user_id, channel_id)
    except KeyError:
        return ResponseUtil.not_found(msg="通道不存在")
    await SettingsService.append_audit(db, user_id=current_user.user_id, action="channel.test_connection", setting_domain="channel", target_id=channel_id, summary={"result": result.get("status"), "correlation_id": result.get("correlation_id")})
    await db.commit()
    return ResponseUtil.success(data=result)


@user_settings_router.post("/channels/{channel_id}/test-delivery")
async def test_channel_delivery(channel_id: str, request: Request, current_user: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    from noesis.services.channel_operations_service import ChannelOperationsService
    await UserService.require_csrf(request)
    try:
        result = await ChannelOperationsService.test_delivery(current_user.user_id, channel_id)
    except KeyError:
        return ResponseUtil.not_found(msg="通道不存在")
    await SettingsService.append_audit(db, user_id=current_user.user_id, action="channel.test_delivery", setting_domain="channel", target_id=channel_id, summary={"result": result.get("status"), "correlation_id": result.get("correlation_id")})
    await db.commit()
    return ResponseUtil.success(data=result)
