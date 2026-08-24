"""用户自定义对话模型 API（挂在 /api/user/llm）。"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Body, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth_dependencies import get_current_user, require_csrf
from server.db import get_db
from server.response import ResponseUtil
from noesis.schemas.login_vo import CurrentUser
from noesis.services.settings_service import SettingsService
from noesis.services.user_llm_service import UserLLMService

user_llm_router = APIRouter(prefix="/api/user/llm", tags=["用户模型"])


# ---------- 请求体 ----------


class ProviderUpsertBody(BaseModel):
    name: str = ""
    api_type: str = "openai"
    base_url: str = ""
    enabled: bool = True
    api_key: Optional[str] = None
    api_key_action: Literal["keep", "replace", "clear"] = "keep"


class ModelUpsertBody(BaseModel):
    model_config = {"extra": "forbid"}

    provider_id: str
    model_id: str
    label: str = ""
    temperature: Optional[float] = None
    context_window: int = 0


# ---------- Provider ----------


@user_llm_router.get("/providers", summary="列出模型服务")
async def list_providers(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    providers = await UserLLMService.list_providers(db, user_id=current_user.user_id)
    return ResponseUtil.success(data={"providers": providers})


@user_llm_router.post("/providers", summary="创建模型服务")
async def create_provider(
    request: Request,
    body: ProviderUpsertBody = Body(default=ProviderUpsertBody()),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_csrf(request)
    provider = await UserLLMService.create_provider(
        db,
        user_id=current_user.user_id,
        name=body.name, api_type=body.api_type, base_url=body.base_url,
        api_key=body.api_key or "", enabled=body.enabled,
    )
    await SettingsService.append_audit(
        db, user_id=current_user.user_id, action="llm.provider.create",
        setting_domain="llm", target_id=provider["provider_id"],
        summary={"fields": ["name", "api_type", "base_url", "api_key"], "secret_action": "set"},
    )
    await db.commit()
    return ResponseUtil.success(msg="已创建", data=provider)


@user_llm_router.put("/providers/{provider_id}", summary="更新模型服务")
async def update_provider(
    provider_id: str,
    request: Request,
    body: ProviderUpsertBody = Body(default=ProviderUpsertBody()),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_csrf(request)
    provider = await UserLLMService.update_provider(
        db,
        user_id=current_user.user_id, provider_id=provider_id,
        name=body.name, api_type=body.api_type, base_url=body.base_url,
        enabled=body.enabled, api_key=body.api_key, api_key_action=body.api_key_action,
    )
    await SettingsService.append_audit(
        db, user_id=current_user.user_id, action="llm.provider.update",
        setting_domain="llm", target_id=provider_id,
        summary={"fields": ["name", "api_type", "base_url", "enabled"], "secret_action": body.api_key_action},
    )
    await db.commit()
    return ResponseUtil.success(msg="已更新", data=provider)


@user_llm_router.delete("/providers/{provider_id}", summary="删除模型服务")
async def delete_provider(
    provider_id: str,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_csrf(request)
    await UserLLMService.delete_provider(db, user_id=current_user.user_id, provider_id=provider_id)
    await SettingsService.append_audit(
        db, user_id=current_user.user_id, action="llm.provider.delete",
        setting_domain="llm", target_id=provider_id,
    )
    await db.commit()
    return ResponseUtil.success(msg="已删除")


@user_llm_router.post("/providers/{provider_id}/test", summary="测试模型服务连通性")
async def test_provider(
    provider_id: str,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_csrf(request)
    result = await UserLLMService.test_provider(db, user_id=current_user.user_id, provider_id=provider_id)
    await SettingsService.append_audit(
        db, user_id=current_user.user_id, action="llm.provider.test",
        setting_domain="llm", target_id=provider_id,
        summary={"ok": result.get("ok")},
    )
    await db.commit()
    return ResponseUtil.success(data=result)


# ---------- Model ----------


@user_llm_router.get("/models", summary="列出自定义模型")
async def list_models(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    models = await UserLLMService.list_models(db, user_id=current_user.user_id)
    return ResponseUtil.success(data={"models": models})


@user_llm_router.post("/models", summary="创建自定义模型")
async def create_model(
    request: Request,
    body: ModelUpsertBody,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_csrf(request)
    entry = await UserLLMService.create_model(
        db,
        user_id=current_user.user_id, provider_id=body.provider_id, model_id=body.model_id,
        label=body.label, temperature=body.temperature, context_window=body.context_window,
    )
    await SettingsService.append_audit(
        db, user_id=current_user.user_id, action="llm.model.create",
        setting_domain="llm", target_id=entry["entry_id"],
        summary={"fields": ["provider_id", "model_id", "label"]},
    )
    await db.commit()
    return ResponseUtil.success(msg="已创建", data=entry)


@user_llm_router.put("/models/{entry_id}", summary="更新自定义模型")
async def update_model(
    entry_id: str,
    request: Request,
    body: ModelUpsertBody,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_csrf(request)
    entry = await UserLLMService.update_model(
        db,
        user_id=current_user.user_id, entry_id=entry_id,
        provider_id=body.provider_id, model_id=body.model_id, label=body.label,
        temperature=body.temperature, context_window=body.context_window,
    )
    await SettingsService.append_audit(
        db, user_id=current_user.user_id, action="llm.model.update",
        setting_domain="llm", target_id=entry_id,
        summary={"fields": ["provider_id", "model_id", "label"]},
    )
    await db.commit()
    return ResponseUtil.success(msg="已更新", data=entry)


@user_llm_router.delete("/models/{entry_id}", summary="删除自定义模型")
async def delete_model(
    entry_id: str,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_csrf(request)
    await UserLLMService.delete_model(db, user_id=current_user.user_id, entry_id=entry_id)
    await SettingsService.append_audit(
        db, user_id=current_user.user_id, action="llm.model.delete",
        setting_domain="llm", target_id=entry_id,
    )
    await db.commit()
    return ResponseUtil.success(msg="已删除")
