"""设置控制面公共 API。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from noesis_server.common.http.response import ResponseUtil
from noesis.schemas.login_vo import CurrentUser
from noesis_server.infrastructure.database.dependency import get_db

from noesis.services.settings_service import SettingsService
from noesis.services.user_service import UserService

settings_router = APIRouter(prefix="/api/user/settings", tags=["用户设置"])


class NotificationWrite(BaseModel):
    event_type: str
    delivery_surface: str
    enabled: bool
    version: int | None = None


class ImportPreviewBody(BaseModel):
    manifest: dict


class ImportApplyBody(ImportPreviewBody):
    preview_id: str


@settings_router.get("/capabilities")
async def get_settings_capabilities(
    _current_user: CurrentUser = Depends(UserService.get_current_user),
):
    capabilities = SettingsService.get_capabilities()
    return ResponseUtil.success(data=capabilities.model_dump())


@settings_router.get("/audit")
async def list_settings_audit(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await SettingsService.list_audit(db, current_user.user_id, page, page_size)
    return ResponseUtil.success(data=result.model_dump())


@settings_router.get("/notifications")
async def list_notifications(current_user: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    from noesis.services.notification_preference_service import NotificationPreferenceService
    return ResponseUtil.success(data={"items": await NotificationPreferenceService.list_preferences(db, current_user.user_id)})


@settings_router.put("/notifications")
async def update_notification(body: NotificationWrite, request: Request, current_user: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    from noesis.services.notification_preference_service import NotificationPreferenceService
    await UserService.require_csrf(request)
    try:
        item = await NotificationPreferenceService.set_preference(db, current_user.user_id, body.event_type, body.delivery_surface, body.enabled, body.version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ResponseUtil.success(msg="已保存", data=item)


@settings_router.get("/diagnostics")
async def get_diagnostics(current_user: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    from noesis.services.settings_diagnostics_service import SettingsDiagnosticsService
    return ResponseUtil.success(data=await SettingsDiagnosticsService.diagnose(db, current_user.user_id))


@settings_router.get("/export")
async def export_settings(current_user: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    from noesis.services.settings_transfer_service import SettingsTransferService
    return ResponseUtil.success(data=await SettingsTransferService.export(db, current_user.user_id))


@settings_router.post("/import/preview")
async def preview_import(body: ImportPreviewBody, request: Request, current_user: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    from noesis.services.settings_transfer_service import SettingsTransferService
    await UserService.require_csrf(request)
    try:
        result = await SettingsTransferService.preview(db, current_user.user_id, body.manifest)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseUtil.success(data=result)


@settings_router.post("/import/apply")
async def apply_import(body: ImportApplyBody, request: Request, current_user: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    from noesis.services.settings_transfer_service import SettingsTransferService
    await UserService.require_csrf(request)
    try:
        result = await SettingsTransferService.apply(db, current_user.user_id, body.manifest, body.preview_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ResponseUtil.success(msg="导入完成", data=result)


@settings_router.post("/reset")
async def reset_settings(request: Request, current_user: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    from noesis.services.settings_transfer_service import SettingsTransferService
    await UserService.require_csrf(request)
    return ResponseUtil.success(msg="已恢复默认设置", data=await SettingsTransferService.reset(db, current_user.user_id))
