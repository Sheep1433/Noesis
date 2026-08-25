"""create machine-memory schema

Revision ID: 202608220001
Revises: 202608210003
Create Date: 2026-08-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "202608220001"
down_revision = "202608210003"
branch_labels = None
depends_on = None

JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def _create_schema(*, include_preference: bool) -> None:
    if include_preference:
        op.create_table(
            "t_memory_user_preference",
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
            *_timestamps(),
            sa.PrimaryKeyConstraint("user_id"),
            comment="Single user-controlled machine-memory preference",
        )
    op.create_table(
        "t_memory_run_snapshot",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("scope_key", sa.String(512), nullable=False),
        sa.Column("source_updated_at", sa.BigInteger(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("schema_version", sa.String(48), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("evidence_json", JSON_VALUE, nullable=True),
        sa.Column("evidence_path", sa.String(768), nullable=True),
        sa.Column("source_token_estimate", sa.Integer(), server_default="0", nullable=False),
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("chunk_metadata", JSON_VALUE, nullable=False),
        sa.Column("coverage", JSON_VALUE, nullable=False),
        sa.Column("capture_status", sa.String(24), server_default="captured", nullable=False),
        sa.Column("processing_status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("error_summary", sa.String(500), nullable=True),
        sa.CheckConstraint("capture_status IN ('captured','partial','failed')", name="ck_memory_snapshot_capture_status"),
        sa.CheckConstraint("processing_status IN ('pending','extracting','consolidating','succeeded','succeeded_no_output','partial','failed','dead','skipped_disabled')", name="ck_memory_snapshot_processing_status"),
        sa.CheckConstraint("chunk_count >= 0 AND source_token_estimate >= 0", name="ck_memory_snapshot_counts"),
        sa.ForeignKeyConstraint(["run_id"], ["t_agent_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index("idx_memory_snapshot_user_scope", "t_memory_run_snapshot", ["user_id", "scope_key", "captured_at"])
    op.create_table(
        "t_memory_item",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("scope_key", sa.String(512), nullable=False),
        sa.Column("memory_type", sa.String(24), nullable=False),
        sa.Column("subject", sa.String(160), nullable=False),
        sa.Column("subject_key", sa.String(192), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("applicability", sa.Text(), server_default="", nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("effective_provenance", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), server_default="candidate", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_id", sa.String(36), nullable=True),
        sa.Column("user_revision", sa.Boolean(), server_default=sa.false(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("memory_type IN ('decision','experience','workflow','gotcha')", name="ck_memory_item_type"),
        sa.CheckConstraint("status IN ('candidate','active','superseded','disabled','invalidated','needs_review')", name="ck_memory_item_status"),
        sa.CheckConstraint("effective_provenance IN ('user','assistant_derived','tool_internal','tool_external')", name="ck_memory_item_provenance"),
        sa.CheckConstraint("version >= 1", name="ck_memory_item_version"),
        sa.CheckConstraint("supersedes_id IS NULL OR supersedes_id <> id", name="ck_memory_item_not_self_supersede"),
        sa.CheckConstraint("status <> 'superseded' OR valid_to IS NOT NULL", name="ck_memory_item_superseded_window"),
        sa.ForeignKeyConstraint(["supersedes_id"], ["t_memory_item.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_memory_item_current_identity",
        "t_memory_item",
        ["user_id", "scope_key", "memory_type", "subject_key"],
        unique=True,
        postgresql_where=sa.text("status <> 'superseded'"),
        sqlite_where=sa.text("status <> 'superseded'"),
    )
    op.create_index(
        "uq_memory_item_supersedes",
        "t_memory_item",
        ["supersedes_id"],
        unique=True,
        postgresql_where=sa.text("supersedes_id IS NOT NULL"),
        sqlite_where=sa.text("supersedes_id IS NOT NULL"),
    )
    op.create_index("idx_memory_item_retrieval", "t_memory_item", ["user_id", "scope_key", "status", "memory_type"])
    op.create_table(
        "t_memory_evidence",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("memory_id", sa.String(36), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("source_kind", sa.String(24), nullable=False),
        sa.Column("source_ref", sa.String(256), nullable=False),
        sa.Column("span_digest", sa.String(64), nullable=False),
        sa.Column("provenance", sa.String(24), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("source_kind IN ('message','tool','artifact','chunk','user_revision')", name="ck_memory_evidence_source_kind"),
        sa.CheckConstraint("provenance IN ('user','assistant_derived','tool_internal','tool_external')", name="ck_memory_evidence_provenance"),
        sa.ForeignKeyConstraint(["memory_id"], ["t_memory_item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["t_memory_run_snapshot.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("memory_id", "snapshot_id", "source_ref", name="uq_memory_evidence_span"),
    )
    op.create_index("idx_memory_evidence_snapshot", "t_memory_evidence", ["snapshot_id", "source_ref"])
    op.create_table(
        "t_memory_relation",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("source_item_id", sa.String(36), nullable=False),
        sa.Column("target_item_id", sa.String(36), nullable=False),
        sa.Column("relation_type", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("relation_type IN ('supersedes','contradicts','derived_from','applies_to')", name="ck_memory_relation_type"),
        sa.CheckConstraint("source_item_id <> target_item_id", name="ck_memory_relation_not_self"),
        sa.ForeignKeyConstraint(["source_item_id"], ["t_memory_item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_item_id"], ["t_memory_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_item_id", "target_item_id", "relation_type", name="uq_memory_relation_edge"),
    )
    op.create_index("idx_memory_relation_user", "t_memory_relation", ["user_id", "source_item_id"])
    op.create_table(
        "t_memory_job",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=True),
        sa.Column("phase", sa.String(24), server_default="capture", nullable=False),
        sa.Column("status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("stage_result", JSON_VALUE, nullable=False),
        sa.Column("coverage", JSON_VALUE, nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(120), nullable=True),
        sa.Column("claim_token", sa.String(36), nullable=True),
        sa.Column("error_summary", sa.String(500), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("phase IN ('capture','extract','consolidate','workspace_sync','index_sync')", name="ck_memory_job_phase"),
        sa.CheckConstraint("status IN ('pending','claimed','succeeded','succeeded_no_output','partial','failed','dead','skipped_disabled')", name="ck_memory_job_status"),
        sa.CheckConstraint("attempts >= 0 AND max_attempts >= 1 AND attempts <= max_attempts", name="ck_memory_job_attempts"),
        sa.ForeignKeyConstraint(["run_id"], ["t_agent_run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["t_memory_run_snapshot.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_memory_job_run"),
    )
    op.create_index("idx_memory_job_claim", "t_memory_job", ["status", "next_retry_at", "lease_until"])
    op.create_index("idx_memory_job_health", "t_memory_job", ["user_id", "status", "updated_at"])
    op.create_table(
        "t_memory_outbox",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("scope_key", sa.String(512), nullable=False),
        sa.Column("memory_id", sa.String(36), nullable=True),
        sa.Column("target", sa.String(16), nullable=False),
        sa.Column("desired_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="8", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(120), nullable=True),
        sa.Column("claim_token", sa.String(36), nullable=True),
        sa.Column("error_summary", sa.String(500), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("target IN ('workspace','index')", name="ck_memory_outbox_target"),
        sa.CheckConstraint("status IN ('pending','claimed','succeeded','failed','dead')", name="ck_memory_outbox_status"),
        sa.CheckConstraint("attempts >= 0 AND max_attempts >= 1 AND attempts <= max_attempts", name="ck_memory_outbox_attempts"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_memory_outbox_claim", "t_memory_outbox", ["target", "status", "next_retry_at", "lease_until"])
    op.create_index("idx_memory_outbox_item", "t_memory_outbox", ["memory_id", "created_at"])
    op.create_table(
        "t_memory_query_trace",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("scope_key", sa.String(512), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("returned_spans", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("evidence_status", sa.String(24), nullable=False),
        sa.Column("failure_category", sa.String(48), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("evidence_status IN ('exact','near','contradicts','insufficient','unavailable')", name="ck_memory_query_trace_status"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_memory_query_trace_retention", "t_memory_query_trace", ["created_at"])
    op.create_index("idx_memory_query_trace_user", "t_memory_query_trace", ["user_id", "created_at"])


def upgrade() -> None:
    _create_schema(include_preference=True)


def downgrade() -> None:
    op.drop_table("t_memory_query_trace")
    op.drop_table("t_memory_outbox")
    op.drop_table("t_memory_job")
    op.drop_table("t_memory_relation")
    op.drop_table("t_memory_evidence")
    op.drop_table("t_memory_item")
    op.drop_table("t_memory_run_snapshot")
    op.drop_table("t_memory_user_preference")
