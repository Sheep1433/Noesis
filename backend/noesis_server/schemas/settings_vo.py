"""设置控制面的公共请求与响应模型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class SecretWriteAction(StrEnum):
    KEEP = "keep"
    REPLACE = "replace"
    CLEAR = "clear"


class SecretWriteCommand(BaseModel):
    action: SecretWriteAction = Field(description="敏感值写入动作")
    value: str | None = Field(default=None, description="仅 replace 时提交的新值")

    @model_validator(mode="after")
    def validate_value(self) -> "SecretWriteCommand":
        if self.action is SecretWriteAction.REPLACE and not (self.value or "").strip():
            raise ValueError("replace 必须提供非空敏感值")
        if self.action is not SecretWriteAction.REPLACE and self.value is not None:
            raise ValueError("keep/clear 不得携带敏感值")
        return self


class SecretSummary(BaseModel):
    configured: bool = Field(description="是否已配置敏感值")
    suffix: str | None = Field(default=None, description="可选的不可逆末尾提示")
    updated_at: str | None = Field(default=None, description="最近更新时间")


class ActionableError(BaseModel):
    code: str = Field(description="稳定的用户行动码")
    message: str = Field(description="用户可理解的错误摘要")
    retryable: bool = Field(default=False, description="是否允许用户重试")
    correlation_id: str | None = Field(default=None, description="服务端关联标识")


class SettingsCapabilities(BaseModel):
    provider_models: bool = Field(description="模型与 Provider 设置是否开放")
    mcp_management: bool = Field(description="MCP 表单管理是否开放")
    automation_operations: bool = Field(description="自动化运行历史是否开放")
    channel_operations: bool = Field(description="通道诊断是否开放")
    agent_context: bool = Field(description="Agent 上下文设置是否开放")
    observability: bool = Field(description="通知与诊断是否开放")
    import_export: bool = Field(description="设置导入导出是否开放")


class SettingsAuditItem(BaseModel):
    id: str
    action: str
    setting_domain: str
    target_id: str | None = None
    summary: dict
    correlation_id: str | None = None
    created_at: int


class SettingsAuditPage(BaseModel):
    items: list[SettingsAuditItem]
    page: int
    page_size: int
    total: int


class ProviderCreate(BaseModel):
    provider_type: Literal["openai", "deepseek", "qwen", "minimax", "opencode"]
    display_name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=8, max_length=500)
    enabled: bool = True
    secret: SecretWriteCommand

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("https://", "http://")):
            raise ValueError("Base URL 必须使用 http 或 https")
        return normalized


class ProviderUpdate(BaseModel):
    provider_type: Literal["openai", "deepseek", "qwen", "minimax", "opencode"]
    display_name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=8, max_length=500)
    enabled: bool
    version: int = Field(ge=1)
    secret: SecretWriteCommand

    _validate_base_url = field_validator("base_url")(ProviderCreate.validate_base_url.__func__)


class ProviderView(BaseModel):
    id: str
    provider_type: str
    display_name: str
    base_url: str
    enabled: bool
    secret: SecretSummary
    version: int
    created_at: int
    updated_at: int


class ProviderProbeResult(BaseModel):
    ok: bool
    checked_at: int
    error_category: Literal["authentication", "timeout", "connection", "invalid_response", "provider", "none"]
    message: str
    model_count: int = 0
    correlation_id: str | None = None


class ProviderModel(BaseModel):
    id: str
    name: str
    capabilities: list[Literal["chat", "vision", "embedding", "rerank"]]


class ModelPurposeBindingWrite(BaseModel):
    provider_id: str
    model_id: str = Field(min_length=1, max_length=200)
    model_name: str = Field(min_length=1, max_length=200)
    capabilities: list[Literal["chat", "vision", "embedding", "rerank"]]


class ModelPurposeBindingView(ModelPurposeBindingWrite):
    purpose: Literal["chat", "vision", "embedding", "rerank"]
    version: int
    updated_at: int
