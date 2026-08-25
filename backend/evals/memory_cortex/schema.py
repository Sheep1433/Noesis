"""Strict fixture and observation schemas for memory evaluation."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    ERROR = "error"
    INTERRUPTED = "interrupted"
    HITL_PENDING = "hitl_pending"


class MemoryType(StrEnum):
    DECISION = "decision"
    EXPERIENCE = "experience"
    WORKFLOW = "workflow"
    GOTCHA = "gotcha"


class Provenance(StrEnum):
    USER = "user"
    ASSISTANT_DERIVED = "assistant_derived"
    TOOL_INTERNAL = "tool_internal"
    TOOL_EXTERNAL = "tool_external"
    SYSTEM = "system"
    MEMORY_RECALL = "memory_recall"


class EvidenceKind(StrEnum):
    USER_GOAL = "user_goal"
    USER_CORRECTION = "user_correction"
    ASSISTANT_CONCLUSION = "assistant_conclusion"
    TOOL_OUTCOME = "tool_outcome"
    ARTIFACT = "artifact"
    VALIDATION = "validation"
    COMPACTION = "compaction"
    MEMORY_RECALL = "memory_recall"


class EvidenceSpan(StrictModel):
    id: str = Field(min_length=1, description="Fixture-local stable source span id")
    kind: EvidenceKind
    provenance: Provenance
    text: str = Field(default="", max_length=4_000)
    terminal: bool = True
    derived_from: list[str] = Field(default_factory=list)


class RunInput(StrictModel):
    status: RunStatus
    memory_enabled: bool = True
    root_run: bool = True
    internal_memory_run: bool = False
    user_cancelled: bool = False
    tool_failure_count: int = Field(default=0, ge=0)
    project_key: str = "repo:fixture"
    agent_profile: str = "SUPER_AGENT_QA"
    evidence: list[EvidenceSpan] = Field(default_factory=list)


class GoldMemoryItem(StrictModel):
    memory_type: MemoryType
    subject: str = Field(min_length=1, max_length=160)
    statement_contains: list[str] = Field(min_length=1)
    statement_contains_any: list[list[str]] = Field(default_factory=list)
    applicability_contains: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(min_length=1)
    expected_state: str = "candidate"
    automatic_injection_eligible: bool = False


class RetrievalMode(StrEnum):
    EXACT = "exact"
    NEAR = "near"
    CONTRADICTS = "contradicts"
    INSUFFICIENT = "insufficient"


class RetrievalProbe(StrictModel):
    query: str = Field(min_length=1)
    mode: RetrievalMode
    relevant_subjects: list[str] = Field(default_factory=list)
    forbidden_subjects: list[str] = Field(default_factory=list)
    expected_abstain: bool = False


class FollowupTask(StrictModel):
    prompt: str
    success_criteria: list[str] = Field(default_factory=list)


class RunMemoryFixture(StrictModel):
    schema_version: str = "run-memory-fixture-v1"
    id: str = Field(min_length=1)
    split: str
    category: str
    description: str
    run: RunInput
    expected_capture: bool
    expected_no_output: bool = False
    gold_items: list[GoldMemoryItem] = Field(default_factory=list)
    expected_operation: str | None = None
    retrieval_probes: list[RetrievalProbe] = Field(default_factory=list)
    followup_task: FollowupTask | None = None
    safety_tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_gold(self) -> "RunMemoryFixture":
        span_ids = {span.id for span in self.run.evidence}
        refs = {ref for item in self.gold_items for ref in item.evidence_refs}
        if not refs <= span_ids:
            raise ValueError(f"gold evidence refs missing from run: {sorted(refs - span_ids)}")
        if not self.expected_capture and (self.gold_items or self.expected_no_output):
            raise ValueError("non-captured fixture cannot define extraction output")
        if self.expected_no_output and self.gold_items:
            raise ValueError("no-output fixture cannot contain gold items")
        return self


class CandidateObservation(StrictModel):
    memory_type: MemoryType
    subject: str
    statement: str
    applicability: str = ""
    evidence_refs: list[str]


class FixtureObservation(StrictModel):
    fixture_id: str
    captured: bool
    processed_chunk_ids: list[str] = Field(default_factory=list)
    failed_chunk_ids: list[str] = Field(default_factory=list)
    expected_chunk_ids: list[str] = Field(default_factory=list)
    candidates: list[CandidateObservation] = Field(default_factory=list)
    operation: str | None = None
    failure_category: str | None = None


__all__ = [
    "CandidateObservation",
    "EvidenceKind",
    "EvidenceSpan",
    "FixtureObservation",
    "FollowupTask",
    "GoldMemoryItem",
    "MemoryType",
    "Provenance",
    "RetrievalMode",
    "RetrievalProbe",
    "RunInput",
    "RunMemoryFixture",
    "RunStatus",
]
