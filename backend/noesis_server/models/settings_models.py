"""设置控制面 ORM；各设置域独立持久化，避免形成不可演进的大 JSON。"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import BigInteger, Boolean, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from noesis_server.infrastructure.database.engine import Base


class TUserProviderConnection(Base):
    __tablename__ = "user_provider_connections"
    __table_args__ = (
        Index("idx_user_provider_connections_user", "user_id"),
        UniqueConstraint("user_id", "display_name", name="uq_user_provider_connections_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_type: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    secret_ciphertext: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    secret_suffix: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    secret_updated_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class TUserModelPurposeBinding(Base):
    __tablename__ = "user_model_purpose_bindings"
    __table_args__ = (
        UniqueConstraint("user_id", "purpose", name="uq_user_model_purpose_binding"),
        Index("idx_user_model_purpose_bindings_provider", "user_id", "provider_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(36), nullable=False)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class TUserScheduledTaskRun(Base):
    __tablename__ = "user_scheduled_task_runs"
    __table_args__ = (
        Index("idx_user_scheduled_task_runs_task", "user_id", "task_id", "created_at"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_user_task_run_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_source: Mapped[str] = mapped_column(String(32), nullable=False)
    retry_of: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    session_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    result_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    delivery_result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    started_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    finished_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class TUserNotificationPreference(Base):
    __tablename__ = "user_notification_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", "event_type", "delivery_surface", name="uq_user_notification_preference"),
        Index("idx_user_notification_preferences_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    delivery_surface: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class TUserSettingsAudit(Base):
    __tablename__ = "user_settings_audit"
    __table_args__ = (Index("idx_user_settings_audit_user_time", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    setting_domain: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
