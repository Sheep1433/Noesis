"""MCP API 请求/响应模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class McpServerCatalogItemVo(BaseModel):
    id: str = Field(..., description="MCP server id")
    source: Literal["platform", "user"] = Field(..., description="来源")
    transport: str = Field(..., description="传输协议")
    url: str | None = Field(None, description="脱敏后的 URL")
    display_name: str | None = Field(None, description="展示名")


class McpServerCatalogResponse(BaseModel):
    servers: list[McpServerCatalogItemVo] = Field(default_factory=list)


class McpServerUpsertRequest(BaseModel):
    transport: Literal["streamable_http", "sse"] = Field(..., description="仅允许 HTTP 类传输")
    url: str = Field(..., description="MCP endpoint URL")
    display_name: str | None = Field(None, description="可选展示名")
    headers: dict[str, str] | None = Field(None, description="可选请求头")
    extra: dict[str, Any] | None = Field(None, description="透传字段")


class McpProbeResponse(BaseModel):
    ok: bool
    tool_count: int = 0
    message: str = ""
