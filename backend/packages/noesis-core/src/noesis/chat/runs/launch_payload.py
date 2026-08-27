"""Launch payload：dispatcher 重建 producer 所需的 schema 化启动载荷。

enable-distributed-sse-pubsub 决策 2：任意 worker 的 create 只写
``queued`` Run + 不可变 launch payload；payload 禁止携带认证秘密、
禁止依赖请求进程内的 ``CreateRunRequest`` / ``CurrentUser``。
dispatcher 从 payload 与 DB 用户记录重建上下文（``RunService.start_queued_run``）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from noesis.schemas.chat_vo import CreateRunRequest
from noesis.schemas.qa_vo import QaQueryRequest

LAUNCH_PAYLOAD_VERSION = 1

# extra 白名单：dispatcher 重建 QaQueryRequest 允许透传的键。
# 认证秘密（cookie/csrf/api key 等）不得进入本列表。
_EXTRA_ALLOWED_KEYS = (
    "file_dict",
    "kb_collections",
    "kb_search_enabled",
    "mcp_servers",
    "enabled_skills",
    "mentions",
    "qa_type",
)

# 禁止字段断言：命中即拒绝构建（防未来误加敏感键）
_FORBIDDEN_KEY_FRAGMENTS = ("password", "cookie", "csrf", "api_key", "token", "secret")


class LaunchPayloadError(ValueError):
    """launch payload 构建或校验失败。"""


def _sanitize_extra(extra: Mapping[str, Any]) -> dict[str, Any]:
    """白名单过滤：未知键静默丢弃（用户请求不因多余键失败）。"""
    sanitized: dict[str, Any] = {}
    for key in _EXTRA_ALLOWED_KEYS:
        if key in extra and extra[key] is not None:
            sanitized[key] = extra[key]
    # 静态不变量：白名单本身不得含敏感键（防未来误加）
    for key in sanitized:
        if any(part in str(key).lower() for part in _FORBIDDEN_KEY_FRAGMENTS):
            raise LaunchPayloadError(
                f"launch payload 白名单含敏感键: {key}"
            )
    return sanitized


@dataclass(frozen=True)
class LaunchPayload:
    """run 启动载荷（JSON 持久化于 t_agent_run.launch_payload）。"""

    schema_version: int
    content: str
    qa_type: str
    session_id: str
    user_id: str
    assistant_message_id: str
    origin: str
    client_request_id: str
    resolved_model: Optional[str]
    created_at: int
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_create_request(
        cls,
        request: CreateRunRequest,
        *,
        user_id: str,
        assistant_message_id: str,
        qa_type: str,
        origin: str,
        resolved_model: Optional[str],
    ) -> LaunchPayload:
        extra = _sanitize_extra(dict(request.extra or {}))
        extra["qa_type"] = qa_type
        return cls(
            schema_version=LAUNCH_PAYLOAD_VERSION,
            content=request.content,
            qa_type=qa_type,
            session_id=request.session_id,
            user_id=user_id,
            assistant_message_id=assistant_message_id,
            origin=origin,
            client_request_id=request.client_request_id,
            resolved_model=resolved_model,
            created_at=int(time.time()),
            extra=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "content": self.content,
            "qa_type": self.qa_type,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "assistant_message_id": self.assistant_message_id,
            "origin": self.origin,
            "client_request_id": self.client_request_id,
            "resolved_model": self.resolved_model,
            "created_at": self.created_at,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LaunchPayload:
        version = int(data.get("schema_version", 0))
        if version != LAUNCH_PAYLOAD_VERSION:
            raise LaunchPayloadError(
                f"unsupported launch payload schema_version={version}"
            )
        return cls(
            schema_version=version,
            content=str(data["content"]),
            qa_type=str(data["qa_type"]),
            session_id=str(data["session_id"]),
            user_id=str(data["user_id"]),
            assistant_message_id=str(data["assistant_message_id"]),
            origin=str(data.get("origin") or "web"),
            client_request_id=str(data.get("client_request_id") or ""),
            resolved_model=(
                str(data["resolved_model"]) if data.get("resolved_model") else None
            ),
            created_at=int(data.get("created_at") or 0),
            extra=_sanitize_extra(dict(data.get("extra") or {})),
        )

    def to_qa_query_request(self) -> QaQueryRequest:
        """重建 QaQueryRequest：model_id 用 create 时冻结的 resolved_model，
        会话默认模型在排队期间变化不影响本 run。"""
        return QaQueryRequest(
            query=self.content,
            qa_type=self.qa_type,
            chat_id=self.session_id,
            file_dict=self.extra.get("file_dict") if isinstance(self.extra.get("file_dict"), dict) else None,
            kb_collections=(
                self.extra.get("kb_collections")
                if isinstance(self.extra.get("kb_collections"), list)
                else None
            ),
            kb_search_enabled=(
                self.extra.get("kb_search_enabled")
                if isinstance(self.extra.get("kb_search_enabled"), bool)
                else None
            ),
            model_id=self.resolved_model,
            mcp_servers=(
                self.extra.get("mcp_servers")
                if isinstance(self.extra.get("mcp_servers"), list)
                else None
            ),
            enabled_skills=(
                self.extra.get("enabled_skills")
                if isinstance(self.extra.get("enabled_skills"), list)
                else None
            ),
            mentions=(
                self.extra.get("mentions")
                if isinstance(self.extra.get("mentions"), list)
                else None
            ),
            extra=dict(self.extra) or None,
        )


__all__ = [
    "LAUNCH_PAYLOAD_VERSION",
    "LaunchPayload",
    "LaunchPayloadError",
]
