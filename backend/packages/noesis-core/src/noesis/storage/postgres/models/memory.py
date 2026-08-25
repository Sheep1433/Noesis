"""Authoritative machine-memory ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from noesis.storage.postgres.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


_JSON = JSON().with_variant(JSONB(), "postgresql")


class TMemoryUserPreference(Base):
    """The single switch controlled by each user."""

    __tablename__ = "t_memory_user_preference"

    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class TMemoryRunSnapshot(Base):
    """Immutable normalized evidence for one eligible terminal root Run."""

    __tablename__ = "t_memory_run_snapshot"
    __table_args__ = (
        CheckConstraint("capture_status IN ('captured','partial','failed')", name="ck_memory_snapshot_capture_status"),
        CheckConstraint(
            "processing_status IN ('pending','extracting','consolidating','succeeded','succeeded_no_output','partial','failed','dead','skipped_disabled')",
            name="ck_memory_snapshot_processing_status",
        ),
        CheckConstraint("chunk_count >= 0 AND source_token_estimate >= 0", name="ck_memory_snapshot_counts"),
        Index("idx_memory_snapshot_user_scope", "user_id", "scope_key", "captured_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("t_agent_run.id", ondelete="CASCADE"), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(512), nullable=False)
    source_updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    schema_version: Mapped[str] = mapped_column(String(48), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_json: Mapped[dict[str, Any] | None] = mapped_column(_JSON, nullable=True)
    evidence_path: Mapped[str | None] = mapped_column(String(768), nullable=True)
    source_token_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False, default=dict)
    coverage: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False, default=dict)
    capture_status: Mapped[str] = mapped_column(String(24), nullable=False, default="captured", server_default="captured")
    processing_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", server_default="pending")
    error_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)


class TMemoryItem(Base):
    """One version of a typed memory item; PostgreSQL remains authoritative."""

    __tablename__ = "t_memory_item"
    __table_args__ = (
        CheckConstraint("memory_type IN ('decision','experience','workflow','gotcha')", name="ck_memory_item_type"),
        CheckConstraint(
            "status IN ('candidate','active','superseded','disabled','invalidated','needs_review')",
            name="ck_memory_item_status",
        ),
        CheckConstraint("effective_provenance IN ('user','assistant_derived','tool_internal','tool_external')", name="ck_memory_item_provenance"),
        CheckConstraint("version >= 1", name="ck_memory_item_version"),
        CheckConstraint("supersedes_id IS NULL OR supersedes_id <> id", name="ck_memory_item_not_self_supersede"),
        CheckConstraint("status <> 'superseded' OR valid_to IS NOT NULL", name="ck_memory_item_superseded_window"),
        Index(
            "uq_memory_item_current_identity",
            "user_id",
            "scope_key",
            "memory_type",
            "subject_key",
            unique=True,
            postgresql_where=text("status <> 'superseded'"),
            sqlite_where=text("status <> 'superseded'"),
        ),
        Index(
            "uq_memory_item_supersedes",
            "supersedes_id",
            unique=True,
            postgresql_where=text("supersedes_id IS NOT NULL"),
            sqlite_where=text("supersedes_id IS NOT NULL"),
        ),
        Index("idx_memory_item_retrieval", "user_id", "scope_key", "status", "memory_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(512), nullable=False)
    memory_type: Mapped[str] = mapped_column(String(24), nullable=False)
    subject: Mapped[str] = mapped_column(String(160), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(192), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    applicability: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_provenance: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="candidate", server_default="candidate")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    supersedes_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("t_memory_item.id", ondelete="SET NULL"), nullable=True)
    user_revision: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class TMemoryEvidence(Base):
    """A source span supporting one memory item."""

    __tablename__ = "t_memory_evidence"
    __table_args__ = (
        CheckConstraint(
            "source_kind IN ('message','tool','artifact','chunk','user_revision')",
            name="ck_memory_evidence_source_kind",
        ),
        CheckConstraint(
            "provenance IN ('user','assistant_derived','tool_internal','tool_external')",
            name="ck_memory_evidence_provenance",
        ),
        UniqueConstraint("memory_id", "snapshot_id", "source_ref", name="uq_memory_evidence_span"),
        Index("idx_memory_evidence_snapshot", "snapshot_id", "source_ref"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    memory_id: Mapped[str] = mapped_column(String(36), ForeignKey("t_memory_item.id", ondelete="CASCADE"), nullable=False)
    snapshot_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("t_memory_run_snapshot.id", ondelete="SET NULL"), nullable=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    span_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance: Mapped[str] = mapped_column(String(24), nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TMemoryRelation(Base):
    __tablename__ = "t_memory_relation"
    __table_args__ = (
        CheckConstraint("relation_type IN ('supersedes','contradicts','derived_from','applies_to')", name="ck_memory_relation_type"),
        CheckConstraint("source_item_id <> target_item_id", name="ck_memory_relation_not_self"),
        UniqueConstraint("source_item_id", "target_item_id", "relation_type", name="uq_memory_relation_edge"),
        Index("idx_memory_relation_user", "user_id", "source_item_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    source_item_id: Mapped[str] = mapped_column(String(36), ForeignKey("t_memory_item.id", ondelete="CASCADE"), nullable=False)
    target_item_id: Mapped[str] = mapped_column(String(36), ForeignKey("t_memory_item.id", ondelete="CASCADE"), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TMemoryJob(Base):
    """Fenced, stage-resumable work for one captured Run."""

    __tablename__ = "t_memory_job"
    __table_args__ = (
        CheckConstraint("phase IN ('capture','extract','consolidate','workspace_sync','index_sync')", name="ck_memory_job_phase"),
        CheckConstraint(
            "status IN ('pending','claimed','succeeded','succeeded_no_output','partial','failed','dead','skipped_disabled')",
            name="ck_memory_job_status",
        ),
        CheckConstraint("attempts >= 0 AND max_attempts >= 1 AND attempts <= max_attempts", name="ck_memory_job_attempts"),
        UniqueConstraint("run_id", name="uq_memory_job_run"),
        Index("idx_memory_job_claim", "status", "next_retry_at", "lease_until"),
        Index("idx_memory_job_health", "user_id", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("t_agent_run.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    snapshot_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("t_memory_run_snapshot.id", ondelete="SET NULL"), nullable=True)
    phase: Mapped[str] = mapped_column(String(24), nullable=False, default="capture", server_default="capture")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", server_default="pending")
    stage_result: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False, default=dict)
    coverage: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default="5")
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class TMemoryOutbox(Base):
    """Desired-state notification for derived workspace and semantic index views."""

    __tablename__ = "t_memory_outbox"
    __table_args__ = (
        CheckConstraint("target IN ('workspace','index')", name="ck_memory_outbox_target"),
        CheckConstraint("status IN ('pending','claimed','succeeded','failed','dead')", name="ck_memory_outbox_status"),
        CheckConstraint("attempts >= 0 AND max_attempts >= 1 AND attempts <= max_attempts", name="ck_memory_outbox_attempts"),
        Index("idx_memory_outbox_claim", "target", "status", "next_retry_at", "lease_until"),
        Index("idx_memory_outbox_item", "memory_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(512), nullable=False)
    memory_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    target: Mapped[str] = mapped_column(String(16), nullable=False)
    desired_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=8, server_default="8")
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class TMemoryQueryTrace(Base):
    """Bounded observability record for one explicit memory query."""

    __tablename__ = "t_memory_query_trace"
    __table_args__ = (
        CheckConstraint(
            "evidence_status IN ('exact','near','contradicts','insufficient','unavailable')",
            name="ck_memory_query_trace_status",
        ),
        Index("idx_memory_query_trace_retention", "created_at"),
        Index("idx_memory_query_trace_user", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(512), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    steps: Mapped[int] = mapped_column(Integer, nullable=False)
    returned_spans: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_status: Mapped[str] = mapped_column(String(24), nullable=False)
    failure_category: Mapped[str | None] = mapped_column(String(48), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


__all__ = [
    "TMemoryEvidence",
    "TMemoryItem",
    "TMemoryJob",
    "TMemoryOutbox",
    "TMemoryQueryTrace",
    "TMemoryRelation",
    "TMemoryRunSnapshot",
    "TMemoryUserPreference",
]
