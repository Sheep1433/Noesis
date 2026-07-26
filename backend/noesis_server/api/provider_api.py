"""用户 Provider 与模型用途设置 API。"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from noesis_server.common.http.response import ResponseUtil
from noesis_server.infrastructure.database.dependency import get_db
from noesis_server.schemas.login_vo import CurrentUser
from noesis_server.schemas.settings_vo import ModelPurposeBindingWrite, ProviderCreate, ProviderUpdate
from noesis_server.services.provider_service import ProviderService
from noesis_server.services.user_service import UserService

provider_router = APIRouter(prefix="/api/user", tags=["模型与 Provider"])


@provider_router.get("/providers")
async def list_providers(current: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    rows = await ProviderService.list(db, current.user_id)
    return ResponseUtil.success(data={"providers": [row.model_dump() for row in rows]})


@provider_router.post("/providers")
async def create_provider(body: ProviderCreate, request: Request, current: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    await UserService.require_csrf(request)
    row = await ProviderService.create(db, current.user_id, body)
    return ResponseUtil.success(data=row.model_dump())


@provider_router.put("/providers/{provider_id}")
async def update_provider(provider_id: str, body: ProviderUpdate, request: Request, current: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    await UserService.require_csrf(request)
    row = await ProviderService.update(db, current.user_id, provider_id, body)
    return ResponseUtil.success(data=row.model_dump())


@provider_router.delete("/providers/{provider_id}")
async def delete_provider(provider_id: str, request: Request, current: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    await UserService.require_csrf(request)
    await ProviderService.delete(db, current.user_id, provider_id)
    return ResponseUtil.success()


@provider_router.post("/providers/{provider_id}/enable")
async def enable_provider(provider_id: str, request: Request, current: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    await UserService.require_csrf(request)
    current_row = await ProviderService.get_row(db, current.user_id, provider_id)
    body = ProviderUpdate(provider_type=current_row.provider_type, display_name=current_row.display_name, base_url=current_row.base_url, enabled=True, version=current_row.version, secret={"action": "keep"})
    row = await ProviderService.update(db, current.user_id, provider_id, body)
    return ResponseUtil.success(data=row.model_dump())


@provider_router.post("/providers/{provider_id}/disable")
async def disable_provider(provider_id: str, request: Request, current: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    await UserService.require_csrf(request)
    current_row = await ProviderService.get_row(db, current.user_id, provider_id)
    body = ProviderUpdate(provider_type=current_row.provider_type, display_name=current_row.display_name, base_url=current_row.base_url, enabled=False, version=current_row.version, secret={"action": "keep"})
    row = await ProviderService.update(db, current.user_id, provider_id, body)
    return ResponseUtil.success(data=row.model_dump())


@provider_router.post("/providers/{provider_id}/test")
async def test_provider(provider_id: str, request: Request, current: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    await UserService.require_csrf(request)
    result = await ProviderService.probe(db, current.user_id, provider_id)
    return ResponseUtil.success(data=result.model_dump())


@provider_router.get("/providers/{provider_id}/models")
async def discover_provider_models(provider_id: str, current: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    models = await ProviderService.discover_models(db, current.user_id, provider_id)
    return ResponseUtil.success(data={"models": [model.model_dump() for model in models]})


@provider_router.get("/model-bindings")
async def list_model_bindings(current: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    rows = await ProviderService.list_bindings(db, current.user_id)
    return ResponseUtil.success(data={"bindings": [row.model_dump() for row in rows]})


@provider_router.put("/model-bindings/{purpose}")
async def bind_model_purpose(purpose: str, body: ModelPurposeBindingWrite, request: Request, current: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    await UserService.require_csrf(request)
    row = await ProviderService.bind(db, current.user_id, purpose, body)
    return ResponseUtil.success(data=row.model_dump())
