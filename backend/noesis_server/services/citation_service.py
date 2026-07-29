"""Message-scoped citation resolve with ownership and exact evidence lookup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from noesis_server.kb.retrieval.service import KbRetrievalService
from noesis_server.models.chat_models import TChatMessage, TChatSession
from noesis.runtime.evidence import citation_telemetry

ResolveStatus = Literal["resolved", "forbidden", "stale", "missing"]


@dataclass(frozen=True)
class CitationResolveResult:
    status: ResolveStatus
    data: dict[str, Any] | None = None


def find_citation_annotation(content: Any, citation_id: str) -> dict[str, Any] | None:
    if not isinstance(content, dict):
        return None
    for part in content.get("parts") or []:
        if not isinstance(part, dict) or part.get("type") != "text":
            continue
        for annotation in part.get("annotations") or []:
            if (
                isinstance(annotation, dict)
                and annotation.get("type") in {"kb_citation", "url_citation"}
                and annotation.get("citation_id") == citation_id
            ):
                return dict(annotation)
    return None


class CitationService:
    @classmethod
    async def resolve(
        cls,
        *,
        message_id: str,
        citation_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> CitationResolveResult:
        row = await db.execute(
            select(TChatMessage, TChatSession)
            .join(TChatSession, TChatSession.id == TChatMessage.session_id)
            .where(and_(
                TChatMessage.id == message_id,
                TChatMessage.role == "assistant",
                TChatMessage.deleted_at.is_(None),
                TChatSession.deleted_at.is_(None),
                TChatMessage.user_id == user_id,
                TChatSession.user_id == user_id,
            ))
        )
        pair = row.first()
        if pair is None:
            citation_telemetry.increment("resolve_missing")
            return CitationResolveResult("missing")
        message, session = pair
        annotation = find_citation_annotation(message.content, citation_id)
        if annotation is None:
            citation_telemetry.increment("resolve_missing")
            return CitationResolveResult("missing")

        if annotation.get("type") == "url_citation":
            url = str(annotation.get("url") or "")
            parsed = urlsplit(url)
            if (
                parsed.scheme.lower() not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
            ):
                citation_telemetry.increment("resolve_missing")
                return CitationResolveResult("missing")
            citation_telemetry.increment("resolve_success")
            return CitationResolveResult("resolved", {
                "citation_id": citation_id,
                "message_id": message_id,
                "title": annotation.get("title") or url,
                "excerpt": annotation.get("excerpt") or "",
                "url": url,
                "verification": annotation.get("verification") or "structural",
                "snapshot_excerpt": annotation.get("excerpt") or "",
            })

        collection_name = str(annotation.get("collection_name") or "")
        document_id = str(annotation.get("document_id") or "")
        document_version_id = str(annotation.get("document_version_id") or "")
        segment_id = str(annotation.get("segment_id") or "")
        if not all((collection_name, document_id, document_version_id, segment_id)):
            citation_telemetry.increment("resolve_missing")
            return CitationResolveResult("missing")

        extra = session.extra if isinstance(session.extra, dict) else {}
        if extra.get("kb_search_enabled") is False:
            citation_telemetry.increment("resolve_forbidden")
            return CitationResolveResult("forbidden")
        allowed = extra.get("kb_collections")
        if isinstance(allowed, list) and allowed and collection_name not in allowed:
            citation_telemetry.increment("resolve_forbidden")
            return CitationResolveResult("forbidden")

        status, live = KbRetrievalService.resolve_evidence(
            collection_name=collection_name,
            document_id=document_id,
            document_version_id=document_version_id,
            segment_id=segment_id,
        )
        if status != "resolved" or live is None:
            citation_telemetry.increment(f"resolve_{status}")
            return CitationResolveResult(status)  # type: ignore[arg-type]
        citation_telemetry.increment("resolve_success")
        return CitationResolveResult("resolved", {
            "citation_id": citation_id,
            "message_id": message_id,
            "title": live["title"] or annotation.get("title") or "",
            "excerpt": live["content"],
            "locator": live["locator"],
            "verification": annotation.get("verification") or "structural",
            "snapshot_excerpt": annotation.get("excerpt") or "",
        })
