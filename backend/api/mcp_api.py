"""MCP 目录、状态与用户配置文件 API。"""

from fastapi import APIRouter, Depends, HTTPException, Query

from common.http.response import ResponseUtil
from schemas.login_vo import CurrentUser
from schemas.mcp_vo import (
    McpConfigUpdateRequest,
    McpServerCatalogItemVo,
    McpServerUpsertRequest,
)
from services.mcp_service import McpService
from services.user_service import UserService

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
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    """整文件保存用户 mcp.json（仅允许 HTTP/SSE transport）。"""
    try:
        cfg = McpService.save_user_config_file(current_user.user_id, body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ResponseUtil.success(msg="已保存", data=cfg.model_dump())


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


@mcp_router.post("/servers/{server_id}/probe")
async def probe_mcp_server(
    server_id: str,
    current_user: CurrentUser = Depends(UserService.get_current_user),
):
    try:
        result = await McpService.probe_server(current_user.user_id, server_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ResponseUtil.success(data=result.model_dump())
