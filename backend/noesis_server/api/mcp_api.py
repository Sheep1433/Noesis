"""MCP 目录、状态与用户配置文件 API。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from noesis_server.common.http.response import ResponseUtil
from noesis.schemas.login_vo import CurrentUser
from noesis.schemas.mcp_vo import (
    McpConfigUpdateRequest,
    McpServerCatalogItemVo,
    McpServerUpsertRequest,
)
from noesis.services.mcp_service import McpService, clear_mcp_probe_cache
from noesis.services.user_service import UserService
from noesis_server.infrastructure.database.dependency import get_db

from noesis.services.settings_service import SettingsService

mcp_router = APIRouter(prefix="/api/mcp", tags=["MCP 模块"])


@mcp_router.get("/servers")
async def list_mcp_servers(
    scope: str = Query(
        "all",
        description="all=平台+用户合并（Composer）；user=仅用户 mcp.json",
    ),
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    if scope not in ("all", "user"):
        raise HTTPException(status_code=400, detail="scope 须为 all 或 user")
    items = McpService.list_servers(current_user.user_id, scope=scope)  # type: ignore[arg-type]
    return ResponseUtil.success(
        data={
            "servers": [
                McpServerCatalogItemVo.model_validate(i.model_dump()).model_dump()
                for i in items
            ]
        }
    )


@mcp_router.get("/servers/status")
async def list_mcp_server_status(
    probe: bool = Query(
        False,
        description="是否探测连通与工具数（会真实握手，较慢；结果短缓存）",
    ),
    scope: str = Query(
        "user",
        description="user=仅用户配置（管理页）；all=平台+用户合并",
    ),
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    if scope not in ("all", "user"):
        raise HTTPException(status_code=400, detail="scope 须为 all 或 user")
    items = await McpService.list_server_status(
        current_user.user_id,
        probe=probe,
        scope=scope,  # type: ignore[arg-type]
    )
    return ResponseUtil.success(
        data={"servers": [i.model_dump() for i in items]},
    )


@mcp_router.get("/config")
async def get_mcp_config(
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """读取当前用户 mcp.json（不存在则 seed 推荐模板）。"""
    cfg = McpService.get_user_config_file(current_user.user_id)
    return ResponseUtil.success(data=cfg.model_dump())


@mcp_router.put("/config")
async def put_mcp_config(
    body: McpConfigUpdateRequest,
    request: Request,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """整文件保存用户 mcp.json（仅允许 HTTP/SSE transport）。"""
    await UserService.require_csrf(request)
    try:
        cfg = McpService.save_user_config_file(current_user.user_id, body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await SettingsService.append_audit(db, user_id=current_user.user_id, action="mcp.config.update", setting_domain="mcp", summary={"fields": ["mcpServers"]})
    await db.commit()
    return ResponseUtil.success(msg="已保存", data=cfg.model_dump())


@mcp_router.put("/servers/{server_id}")
async def upsert_mcp_server(
    server_id: str,
    body: McpServerUpsertRequest,
    request: Request,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await UserService.require_csrf(request)
    try:
        item = McpService.upsert_user_server(current_user.user_id, server_id, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await SettingsService.append_audit(db, user_id=current_user.user_id, action="mcp.server.update", setting_domain="mcp", target_id=server_id, summary={"fields": ["transport", "url", "display_name", "enabled"], "headers_action": body.headers_action})
    await db.commit()
    return ResponseUtil.success(
        msg="已保存",
        data=McpServerCatalogItemVo.model_validate(item.model_dump()).model_dump(),
    )


@mcp_router.delete("/servers/{server_id}")
async def delete_mcp_server(
    server_id: str,
    request: Request,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await UserService.require_csrf(request)
    try:
        McpService.delete_user_server(current_user.user_id, server_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    await SettingsService.append_audit(db, user_id=current_user.user_id, action="mcp.server.delete", setting_domain="mcp", target_id=server_id)
    await db.commit()
    return ResponseUtil.success(msg="已删除")


@mcp_router.post("/servers/{server_id}/probe")
async def probe_mcp_server(
    server_id: str,
    request: Request,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    await UserService.require_csrf(request)
    try:
        result = await McpService.probe_server(current_user.user_id, server_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ResponseUtil.success(data=result.model_dump())


@mcp_router.post("/servers/{server_id}/enable")
async def enable_mcp_server(server_id: str, request: Request, current_user: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    await UserService.require_csrf(request)
    try:
        item = McpService.set_user_server_enabled(current_user.user_id, server_id, True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await SettingsService.append_audit(db, user_id=current_user.user_id, action="mcp.server.enable", setting_domain="mcp", target_id=server_id)
    await db.commit()
    return ResponseUtil.success(data=McpServerCatalogItemVo.model_validate(item.model_dump()).model_dump())


@mcp_router.post("/servers/{server_id}/disable")
async def disable_mcp_server(server_id: str, request: Request, current_user: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    await UserService.require_csrf(request)
    try:
        item = McpService.set_user_server_enabled(current_user.user_id, server_id, False)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await SettingsService.append_audit(db, user_id=current_user.user_id, action="mcp.server.disable", setting_domain="mcp", target_id=server_id)
    await db.commit()
    return ResponseUtil.success(data=McpServerCatalogItemVo.model_validate(item.model_dump()).model_dump())


@mcp_router.get("/servers/{server_id}/tools")
async def list_mcp_server_tools(server_id: str, refresh: bool = Query(False), current_user: CurrentUser = Depends(UserService.get_current_user)):
    try:
        if refresh:
            clear_mcp_probe_cache()
        tools = await McpService.list_server_tools(current_user.user_id, server_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="MCP 工具目录刷新超时") from exc
    return ResponseUtil.success(data={"tools": [tool.model_dump() for tool in tools]})
