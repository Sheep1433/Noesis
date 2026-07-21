"""MCP 目录与用户配置 API。"""

from fastapi import APIRouter, Depends, HTTPException

from common.http.response import ResponseUtil
from schemas.login_vo import CurrentUser
from schemas.mcp_vo import (
    McpProbeResponse,
    McpServerCatalogItemVo,
    McpServerCatalogResponse,
    McpServerUpsertRequest,
)
from services.mcp_service import McpService
from services.user_service import UserService

mcp_router = APIRouter(prefix="/api/mcp", tags=["MCP 模块"])


@mcp_router.get("/servers", response_model=McpServerCatalogResponse)
async def list_mcp_servers(
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    items = McpService.list_servers(current_user.user_id)
    return McpServerCatalogResponse(
        servers=[McpServerCatalogItemVo.model_validate(i.model_dump()) for i in items]
    )


@mcp_router.put("/servers/{server_id}")
async def upsert_mcp_server(
    server_id: str,
    body: McpServerUpsertRequest,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    try:
        item = McpService.upsert_user_server(current_user.user_id, server_id, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ResponseUtil.success(
        msg="已保存",
        data=McpServerCatalogItemVo.model_validate(item.model_dump()).model_dump(),
    )


@mcp_router.delete("/servers/{server_id}")
async def delete_mcp_server(
    server_id: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    try:
        McpService.delete_user_server(current_user.user_id, server_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return ResponseUtil.success(msg="已删除")


@mcp_router.post("/servers/{server_id}/probe", response_model=McpProbeResponse)
async def probe_mcp_server(
    server_id: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    try:
        return await McpService.probe_server(current_user.user_id, server_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
