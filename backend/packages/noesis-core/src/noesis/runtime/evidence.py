"""Run-local retrieval evidence contract and deterministic manifest."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PageLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["page"] = "page"
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> "PageLocator":
        if self.page_end < self.page_start:
            raise ValueError("page_end must be greater than or equal to page_start")
        return self


class CharLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["char"] = "char"
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_range(self) -> "CharLocator":
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        return self


class BboxLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["bbox"] = "bbox"
    page: int = Field(ge=1)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> "BboxLocator":
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("bbox must stay within normalized page bounds")
        return self


class HeaderLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["header"] = "header"
    path: list[str] = Field(min_length=1)


EvidenceLocator = Annotated[
    Union[PageLocator, CharLocator, BboxLocator, HeaderLocator],
    Field(discriminator="type"),
]


class EvidenceEnvelope(BaseModel):
    """Validated retrieval source before platform-local identity allocation."""

    model_config = ConfigDict(extra="forbid")

    source_type: Literal["knowledge_base", "web"] = "knowledge_base"
    collection_name: str | None = None
    document_id: str | None = None
    document_version_id: str | None = None
    segment_id: str | None = None
    url: str | None = None
    title: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    locator: EvidenceLocator | None = None
    score: float | None = None
    recall_score: float | None = None
    rerank_score: float | None = None
    search_mode: str | None = None

    @model_validator(mode="after")
    def validate_identity(self) -> "EvidenceEnvelope":
        if self.source_type == "knowledge_base":
            if not all((self.collection_name, self.document_id, self.document_version_id, self.segment_id)):
                raise ValueError("knowledge-base evidence requires collection/document/version/segment")
        elif not self.url:
            raise ValueError("web evidence requires url")
        return self

    def canonical_identity(self) -> str:
        identity = (
            ["web", self.url]
            if self.source_type == "web"
            else ["knowledge_base", self.document_id, self.document_version_id, self.segment_id]
        )
        return json.dumps(
            identity,
            ensure_ascii=False,
            separators=(",", ":"),
        )


class RetrievalManifestEntry(EvidenceEnvelope):
    evidence_id: str
    tool_call_ids: list[str] = Field(default_factory=list)


class EvidenceIdCollisionError(RuntimeError):
    pass


class RetrievalManifest:
    """Thread-safe manifest shared by all retrieval tool calls in one run."""

    def __init__(self, *, run_salt: str | None = None) -> None:
        self.run_salt = run_salt or secrets.token_hex(16)
        self._lock = threading.RLock()
        self._by_identity: dict[str, RetrievalManifestEntry] = {}
        self._identity_by_id: dict[str, str] = {}
        self._externally_assigned = False

    def _derive_id(self, canonical_identity: str) -> str:
        digest = hmac.new(
            self.run_salt.encode("utf-8"),
            canonical_identity.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"ev_{digest[:40]}"

    def register(
        self,
        envelope: EvidenceEnvelope,
        *,
        tool_call_id: str,
    ) -> RetrievalManifestEntry:
        canonical = envelope.canonical_identity()
        with self._lock:
            existing = self._by_identity.get(canonical)
            if existing is not None:
                if tool_call_id and tool_call_id not in existing.tool_call_ids:
                    existing.tool_call_ids.append(tool_call_id)
                    existing.tool_call_ids.sort()
                return existing.model_copy(deep=True)

            evidence_id = self._derive_id(canonical)
            occupied = self._identity_by_id.get(evidence_id)
            if occupied is not None and occupied != canonical:
                raise EvidenceIdCollisionError(
                    f"evidence id collision: {evidence_id}"
                )
            entry = RetrievalManifestEntry(
                **envelope.model_dump(),
                evidence_id=evidence_id,
                tool_call_ids=sorted({tool_call_id}) if tool_call_id else [],
            )
            self._by_identity[canonical] = entry
            self._identity_by_id[evidence_id] = canonical
            return entry.model_copy(deep=True)

    def get(self, evidence_id: str) -> RetrievalManifestEntry | None:
        with self._lock:
            canonical = self._identity_by_id.get(evidence_id)
            entry = self._by_identity.get(canonical) if canonical else None
            return entry.model_copy(deep=True) if entry else None

    def get_by_envelope(self, envelope: EvidenceEnvelope) -> RetrievalManifestEntry | None:
        with self._lock:
            entry = self._by_identity.get(envelope.canonical_identity())
            return entry.model_copy(deep=True) if entry else None

    def ingest(self, entry: RetrievalManifestEntry) -> RetrievalManifestEntry:
        """从已持久化的 assistant snapshot 恢复原有 entry。"""
        canonical = entry.canonical_identity()
        with self._lock:
            self._externally_assigned = True
            occupied_identity = self._identity_by_id.get(entry.evidence_id)
            if occupied_identity is not None and occupied_identity != canonical:
                raise EvidenceIdCollisionError(
                    f"evidence id collision: {entry.evidence_id}"
                )
            existing = self._by_identity.get(canonical)
            if existing is not None:
                if existing.evidence_id != entry.evidence_id:
                    raise EvidenceIdCollisionError(
                        "same evidence identity received with different evidence ids"
                    )
                existing.tool_call_ids = sorted(
                    set(existing.tool_call_ids) | set(entry.tool_call_ids)
                )
                return existing.model_copy(deep=True)
            copied = entry.model_copy(deep=True)
            self._by_identity[canonical] = copied
            self._identity_by_id[copied.evidence_id] = canonical
            return copied.model_copy(deep=True)

    def entries(self) -> list[RetrievalManifestEntry]:
        with self._lock:
            return [
                item.model_copy(deep=True)
                for item in sorted(
                    self._by_identity.values(), key=lambda entry: entry.evidence_id
                )
            ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_salt": self.run_salt,
            "externally_assigned": self._externally_assigned,
            "entries": [entry.model_dump(mode="json") for entry in self.entries()],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RetrievalManifest":
        manifest = cls(run_salt=str(raw.get("run_salt") or ""))
        manifest._externally_assigned = bool(raw.get("externally_assigned"))
        for item in raw.get("entries") or []:
            entry = RetrievalManifestEntry.model_validate(item)
            canonical = entry.canonical_identity()
            expected = manifest._derive_id(canonical)
            if not manifest._externally_assigned and entry.evidence_id != expected:
                raise ValueError("persisted evidence_id does not match run manifest salt")
            occupied = manifest._identity_by_id.get(entry.evidence_id)
            if occupied is not None and occupied != canonical:
                raise EvidenceIdCollisionError(
                    f"persisted evidence id collision: {entry.evidence_id}"
                )
            manifest._by_identity[canonical] = entry
            manifest._identity_by_id[entry.evidence_id] = canonical
        return manifest
