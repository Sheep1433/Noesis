"""Machine-memory API and structured model-output schemas."""

from __future__ import annotations

from typing import Any, Literal
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MemoryType = Literal["decision", "experience", "workflow", "gotcha"]
MemoryProvenance = Literal[
    "user", "assistant_derived", "tool_internal", "tool_external"
]


class StrictMemoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CortexPreferenceResponse(StrictMemoryModel):
    enabled: bool = Field(description="用户是否启用机器经验记忆")


class CortexPreferenceUpdate(StrictMemoryModel):
    enabled: bool = Field(description="是否启用机器经验记忆")


class MemorySourceSpan(StrictMemoryModel):
    id: str = Field(min_length=1, max_length=96, description="Snapshot 内稳定 span ID")
    source_ref: str = Field(min_length=1, max_length=256, description="受限来源坐标")
    kind: Literal[
        "user_goal",
        "user_correction",
        "assistant_conclusion",
        "tool_outcome",
        "artifact",
        "validation",
        "compaction",
    ] = Field(description="证据片段类型")
    provenance: MemoryProvenance = Field(description="直接来源类别")
    effective_provenance: MemoryProvenance = Field(
        description="传播低信任后的有效来源类别"
    )
    text: str = Field(default="", max_length=4_000, description="脱敏且有界的证据文本")
    digest: str = Field(
        min_length=64, max_length=64, description="完整规范化片段 SHA-256"
    )
    derived_from: list[str] = Field(
        default_factory=list, max_length=32, description="支撑本片段的 snapshot span ID"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="不含密钥与服务端路径的结构化 outcome"
    )


class RunSnapshotPayload(StrictMemoryModel):
    schema_version: Literal["run-memory-snapshot-v1"] = Field(
        default="run-memory-snapshot-v1",
        description="Run evidence snapshot schema 版本",
    )
    run_id: str = Field(min_length=1, max_length=36, description="来源 Agent Run ID")
    user_id: str = Field(min_length=1, max_length=36, description="来源用户 ID")
    session_id: str = Field(min_length=1, max_length=36, description="来源会话 ID")
    scope_key: str = Field(
        min_length=1, max_length=512, description="规范化 Agent 与项目作用域"
    )
    source_watermark: int = Field(ge=0, description="来源 Run 的更新时间水位")
    spans: list[MemorySourceSpan] = Field(
        default_factory=list, description="不可变且有来源的证据片段"
    )
    recalled_memory_ids: list[str] = Field(
        default_factory=list,
        max_length=100,
        description="本 Run 已召回并须排除再提取的记忆 ID",
    )
    content_digest: str = Field(
        min_length=64, max_length=64, description="Snapshot 规范化内容 SHA-256"
    )
    token_estimate: int = Field(ge=0, description="Snapshot 估算 token 数")
    compaction_covered: bool = Field(
        default=False, description="Snapshot 是否包含 compaction 覆盖信息"
    )


class MemoryChunk(StrictMemoryModel):
    chunk_id: str = Field(
        min_length=64, max_length=64, description="结构化分块稳定 SHA-256 ID"
    )
    ordinal: int = Field(ge=0, description="分块在 Snapshot 内的稳定序号")
    span_ids: list[str] = Field(min_length=1, description="该分块包含的证据 span ID")
    token_estimate: int = Field(ge=0, description="分块估算 token 数")
    text: str = Field(description="供结构化提取模型读取的有界证据文本")


class MemoryCandidate(StrictMemoryModel):
    memory_type: MemoryType = Field(
        description=(
            "decision=明确用户/产品选择；experience=瞬态失败后的修复、部分进展或验证产物；"
            "workflow=带顺序、验证和停止条件的步骤；gotcha=修复后仍持续存在的权限、module、interface 或环境边界"
        )
    )
    subject: str = Field(
        min_length=2, max_length=160, description="简短、稳定、可检索的主题，不含 ID"
    )
    statement: str = Field(
        min_length=4,
        max_length=2_000,
        description="可供后续任务直接使用且完全由证据支持的结论",
    )
    applicability: str = Field(
        default="",
        max_length=1_000,
        description="结论适用的项目条件、失败条件和停止边界",
    )
    evidence_refs: list[str] = Field(
        min_length=1,
        max_length=16,
        description=(
            "支持完整结论的当前 chunk span ID；必须包含支撑选择/失败、修复与验证的全部相关 span，"
            "不得遗漏用于确认结论的 validation，也不得添加无关 span 或 chunk 外 ID"
        ),
    )
    confidence_reason: str = Field(
        min_length=2,
        max_length=500,
        description="解释这些证据为何足以支持结论，不新增事实",
    )
    proposed_relation: (
        Literal["supersedes", "contradicts", "derived_from", "applies_to"] | None
    ) = Field(default=None, description="可选关系建议，最终由代码裁决")

    @field_validator("subject", "statement", "applicability", "confidence_reason")
    @classmethod
    def reject_role_markers_and_secrets(cls, value: str) -> str:
        lowered = value.casefold()
        forbidden = (
            "system:",
            "assistant:",
            "developer:",
            "bearer ",
            "api_key=",
            "password=",
        )
        if any(marker in lowered for marker in forbidden):
            raise ValueError("memory text contains a forbidden marker")
        return value.strip()


class MemoryCandidateBatch(StrictMemoryModel):
    candidates: list[MemoryCandidate] = Field(
        default_factory=list, max_length=20, description="当前证据分块提取出的候选记忆"
    )


class ValidatedMemoryCandidate(StrictMemoryModel):
    memory_type: MemoryType = Field(description="经代码校验的机器记忆类型")
    subject: str = Field(description="经代码校验的稳定主题")
    subject_key: str = Field(
        min_length=64, max_length=64, description="规范化主题 SHA-256"
    )
    statement: str = Field(description="经证据支持的可复用结论")
    applicability: str = Field(default="", description="结论适用条件与停止边界")
    evidence_refs: list[str] = Field(
        description="已验证属于当前 Snapshot 的证据 span ID"
    )
    effective_provenance: MemoryProvenance = Field(
        description="全部支撑证据传播后的最低信任来源"
    )
    confidence_reason: str = Field(description="候选成立的证据理由")
    proposed_relation: (
        Literal["supersedes", "contradicts", "derived_from", "applies_to"] | None
    ) = Field(default=None, description="受限关系建议")
    content_digest: str = Field(
        min_length=64, max_length=64, description="候选规范化内容 SHA-256"
    )
    chunk_ids: list[str] = Field(min_length=1, description="产生该候选的稳定 chunk ID")


class MemorySourceResponse(StrictMemoryModel):
    memory_id: str = Field(description="记忆条目 ID")
    evidence_id: str = Field(description="证据记录 ID")
    availability: Literal["available", "source_deleted", "retention_expired"] = Field(
        description="来源当前可用状态"
    )
    source_kind: Literal["message", "tool", "artifact", "chunk", "user_revision"] = (
        Field(description="来源对象类型")
    )
    source_ref: str | None = Field(
        default=None, description="受限且不含服务端路径的来源坐标"
    )
    excerpt: str | None = Field(default=None, description="脱敏且有界的来源摘录")
    provenance: MemoryProvenance | None = Field(
        default=None, description="来源信任类别"
    )
    source_digest: str | None = Field(default=None, description="来源片段 SHA-256")
    role: Literal["user", "assistant", "tool"] | None = Field(
        default=None, description="消息来源角色"
    )
    tool_outcome: dict[str, Any] | None = Field(
        default=None, description="脱敏且有界的结构化工具结果"
    )
    captured_at: datetime | None = Field(
        default=None, description="来源 Snapshot 捕获时间"
    )


class MemorySearchInput(StrictMemoryModel):
    query: str = Field(
        min_length=1, max_length=500, description="要核对的历史经验或证据问题"
    )
    memory_types: list[MemoryType] = Field(
        default_factory=list, max_length=4, description="记忆类型过滤"
    )
    include_history: bool = Field(default=False, description="是否包含待确认和历史版本")
    statuses: list[
        Literal[
            "candidate",
            "active",
            "needs_review",
            "superseded",
            "disabled",
            "invalidated",
        ]
    ] = Field(
        default_factory=list,
        max_length=6,
        description="显式状态过滤；历史状态需同时开启 include_history",
    )
    source_types: list[
        Literal["message", "tool", "artifact", "chunk", "user_revision"]
    ] = Field(default_factory=list, max_length=5, description="返回来源类型过滤")
    project_scope: Literal["current_project"] = Field(
        default="current_project", description="只能查询 Runtime 绑定的当前项目"
    )
    expand_evidence: bool = Field(default=True, description="是否返回有界来源引用")
    since: datetime | None = Field(default=None, description="只查此时间之后验证的条目")
    until: datetime | None = Field(default=None, description="只查此时间之前验证的条目")
    top_k: int = Field(default=5, ge=1, le=10, description="最多返回的记忆条目数")

    @model_validator(mode="after")
    def validate_status_scope(self) -> "MemorySearchInput":
        if not self.include_history and any(
            status != "active" for status in self.statuses
        ):
            raise ValueError("历史状态查询需要开启 include_history")
        return self


class MemoryDeepQueryItem(StrictMemoryModel):
    memory_id: str = Field(description="命中的记忆 ID")
    memory_type: MemoryType = Field(description="命中的记忆类型")
    status: Literal[
        "candidate", "active", "needs_review", "superseded", "disabled", "invalidated"
    ] = Field(description="命中的当前治理状态")
    score: float = Field(ge=0, le=1, description="归一化相关性分数")
    source_types: list[
        Literal["message", "tool", "artifact", "chunk", "user_revision"]
    ] = Field(default_factory=list, description="命中条目的可用来源类型")


class MemoryDeepQueryResponse(StrictMemoryModel):
    bulletin: str = Field(description="有界且带治理语义的记忆结论")
    memory_ids: list[str] = Field(description="参与结果的记忆 ID")
    source_spans: list[str] = Field(description="可按需展开的来源 span handle")
    evidence_status: Literal[
        "exact", "near", "contradicts", "insufficient", "unavailable"
    ] = Field(description="证据充分性与冲突状态")
    items: list[MemoryDeepQueryItem] = Field(
        default_factory=list, description="结构化命中条目"
    )
    error: str | None = Field(
        default=None, description="用户可理解且不泄露内部实现的错误"
    )


class MemorySourceInput(StrictMemoryModel):
    memory_id: str = Field(min_length=1, max_length=36, description="要追溯的记忆 ID")
    evidence_id: str = Field(min_length=1, max_length=36, description="要读取的证据 ID")


MemoryStatus = Literal[
    "candidate", "active", "superseded", "disabled", "invalidated", "needs_review"
]


class MemoryEvidenceSummary(StrictMemoryModel):
    id: str = Field(description="证据记录 ID")
    source_kind: Literal["message", "tool", "artifact", "chunk", "user_revision"] = (
        Field(description="证据来源类型")
    )
    provenance: MemoryProvenance = Field(description="证据来源信任类别")
    created_at: datetime = Field(description="证据记录时间")


class MemoryItemResponse(StrictMemoryModel):
    id: str = Field(description="记忆条目 ID")
    memory_type: MemoryType = Field(description="记忆类型")
    status: MemoryStatus = Field(description="当前治理状态")
    subject: str = Field(description="稳定可检索主题")
    statement: str = Field(description="当前版本结论")
    applicability: str = Field(description="结论适用条件")
    scope_id: str = Field(description="不暴露内部路径的项目作用域摘要 ID")
    scope_label: str = Field(description="用户可理解的项目作用域标签")
    effective_provenance: MemoryProvenance = Field(description="当前有效来源信任类别")
    version: int = Field(description="当前记忆版本号")
    valid_from: datetime = Field(description="当前版本生效时间")
    valid_to: datetime | None = Field(default=None, description="当前版本失效时间")
    last_verified_at: datetime | None = Field(default=None, description="最近验证时间")
    user_revision: bool = Field(description="当前版本是否由用户直接修订")
    evidence_count: int = Field(description="支持当前条目的独立 Run 数")
    evidence: list[MemoryEvidenceSummary] = Field(
        default_factory=list, description="有界证据摘要"
    )


class MemoryItemUpdate(StrictMemoryModel):
    statement: str = Field(
        min_length=4, max_length=2_000, description="用户修订后的记忆内容"
    )
    applicability: str = Field(
        default="", max_length=1_000, description="修订后的适用条件"
    )

    @field_validator("statement", "applicability")
    @classmethod
    def reject_secrets(cls, value: str) -> str:
        lowered = value.casefold()
        if any(
            marker in lowered
            for marker in ("bearer ", "api_key=", "password=", "secret=")
        ):
            raise ValueError("memory text contains sensitive material")
        return value.strip()


class MemoryStateResponse(StrictMemoryModel):
    id: str = Field(description="记忆条目 ID")
    status: MemoryStatus = Field(description="状态操作后的治理状态")


class MemoryProcessingHealthResponse(StrictMemoryModel):
    last_capture_at: datetime | None = Field(
        default=None, description="最近成功捕获时间"
    )
    last_consolidation_at: datetime | None = Field(
        default=None, description="最近成功整理时间"
    )
    pending: int = Field(default=0, description="等待或正在处理的任务数")
    partial: int = Field(default=0, description="部分处理任务数")
    failed: int = Field(default=0, description="可重试失败任务数")
    dead: int = Field(default=0, description="达到最大尝试次数的任务数")
    skipped: int = Field(default=0, description="因用户关闭而停止的任务数")
    workspace_pending: int = Field(default=0, description="待同步文件视图事件数")
    index_pending: int = Field(default=0, description="待同步检索索引事件数")
    workspace_failed: int = Field(default=0, description="无法继续重试的文件视图事件数")
    index_failed: int = Field(default=0, description="无法继续重试的检索索引事件数")
    derived_view_lag_seconds: int | None = Field(
        default=None, description="最早派生视图延迟秒数"
    )


__all__ = [
    "CortexPreferenceResponse",
    "CortexPreferenceUpdate",
    "MemoryCandidate",
    "MemoryCandidateBatch",
    "MemoryChunk",
    "MemoryProvenance",
    "MemorySourceSpan",
    "MemorySourceResponse",
    "MemorySearchInput",
    "MemoryDeepQueryResponse",
    "MemorySourceInput",
    "MemoryStatus",
    "MemoryEvidenceSummary",
    "MemoryItemResponse",
    "MemoryItemUpdate",
    "MemoryStateResponse",
    "MemoryProcessingHealthResponse",
    "MemoryType",
    "RunSnapshotPayload",
    "ValidatedMemoryCandidate",
]
