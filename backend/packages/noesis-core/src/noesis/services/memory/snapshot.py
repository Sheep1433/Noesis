"""Build immutable, provenance-aware extraction input from terminal Run evidence."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePath
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.repositories.machine_memory_repository import CaptureSource, MachineMemoryRepository
from noesis.schemas.memory import MemorySourceSpan, RunSnapshotPayload
from noesis.services.memory.scope import resolve_scope_key


_SECRET_RE = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+|((?:api[_-]?key|token|password|secret)\s*[:=]\s*)[^\s,;]+"
)
_CORRECTION_RE = re.compile(r"纠正|改成|不要|不应该|之前.{0,12}(?:错|不对)|instead|do not", re.I)
_TERMINAL_TOOL_STATES = {"completed", "success", "error", "failed", "rejected", "cancelled", "timeout"}
_MAX_SPAN_TEXT = 4_000
_MAX_TOOL_TEXT = 1_200


def _compact(value: Any) -> str:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value or "").strip()


def _redact(value: str) -> str:
    return _SECRET_RE.sub(lambda match: f"{match.group(1) or match.group(2)}[REDACTED]", value)


def _safe_text(value: Any, limit: int) -> tuple[str, str]:
    normalized = _compact(value)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    redacted = _redact(normalized)
    return redacted if len(redacted) <= limit else f"{redacted[: limit - 1]}…", digest


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content = content.get("parts")
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for part in content:
        if isinstance(part, dict) and part.get("type") in {"text", "text-delta"}:
            text = part.get("content") or part.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text)
    return "\n".join(chunks)


def _span_id(run_id: str, source_ref: str, kind: str, digest: str) -> str:
    value = f"{run_id}\0{source_ref}\0{kind}\0{digest}"
    return f"span-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:32]}"


def _tool_provenance(part: dict[str, Any]) -> str:
    provider = str(part.get("_provider_key") or "unknown").casefold()
    name = str(part.get("name") or "").casefold()
    if provider.startswith("mcp:") or name in {"web_search", "search_web", "fetch_url", "read_url"}:
        return "tool_external"
    return "tool_internal"


def _tool_kind(part: dict[str, Any]) -> str:
    name = str(part.get("name") or "").casefold()
    if name in {"write_file", "edit_file", "apply_patch", "create_file"}:
        return "artifact"
    arguments = part.get("input") if isinstance(part.get("input"), dict) else {}
    command = str(arguments.get("command") or arguments.get("cmd") or "").casefold()
    if name in {"execute", "exec", "shell"} and re.search(
        r"(?:^|\s)(?:pytest|test|lint|build|typecheck|mypy|ruff|eslint)(?:\s|$)", command
    ):
        return "validation"
    return "tool_outcome"


def _logical_path(part: dict[str, Any]) -> str | None:
    arguments = part.get("input") if isinstance(part.get("input"), dict) else {}
    value = next(
        (arguments.get(key) for key in ("path", "file_path", "filename") if arguments.get(key)),
        None,
    )
    if value is None:
        value = next(
            (part.get(key) for key in ("path", "file_path", "filename") if part.get(key)),
            None,
        )
    if value is None:
        return None
    path = PurePath(str(value))
    if path.is_absolute():
        return path.name
    safe_parts = [part for part in path.parts if part not in {"", ".", ".."}]
    return "/".join(safe_parts[-6:]) or None


def _memory_ids(part: dict[str, Any]) -> list[str]:
    if part.get("type") != "retrieval" or not isinstance(part.get("results"), list):
        return []
    ids: list[str] = []
    for result in part["results"]:
        if not isinstance(result, dict):
            continue
        value = result.get("memory_id")
        if value is None and str(result.get("source") or "").casefold() in {"memory", "cortex"}:
            value = result.get("id")
        if value:
            ids.append(str(value))
    return ids


def _memory_tool_ids(part: dict[str, Any]) -> list[str]:
    if str(part.get("name") or "") not in {"search_memory", "get_memory_source"}:
        return []
    output = part.get("output")
    if not isinstance(output, str):
        return []
    try:
        payload = json.loads(output)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    values = payload.get("memory_ids") if isinstance(payload, dict) else None
    return [str(value) for value in values or [] if value]


class RunSnapshotBuilder:
    @classmethod
    async def build(cls, db: AsyncSession, run_id: str) -> RunSnapshotPayload:
        source = await MachineMemoryRepository(db).load_capture_source(run_id)
        if source is None:
            raise LookupError("terminal Run source is unavailable")
        return cls.from_source(source)

    @staticmethod
    def from_source(source: CaptureSource) -> RunSnapshotPayload:
        run = source.run
        scope_key = resolve_scope_key(
            user_id=str(run.user_id),
            session_id=str(run.session_id),
            agent_profile=run.qa_type,
        )
        spans: list[MemorySourceSpan] = []
        seen: set[tuple[str, str]] = set()

        def append_span(
            *,
            source_ref: str,
            kind: str,
            provenance: str,
            text: Any,
            limit: int = _MAX_SPAN_TEXT,
            effective_provenance: str | None = None,
            derived_from: list[str] | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> MemorySourceSpan | None:
            safe, digest = _safe_text(text, limit)
            if not safe or (kind, digest) in seen:
                return None
            seen.add((kind, digest))
            span = MemorySourceSpan(
                id=_span_id(run.id, source_ref, kind, digest),
                source_ref=source_ref,
                kind=kind,
                provenance=provenance,
                effective_provenance=effective_provenance or provenance,
                text=safe,
                digest=digest,
                derived_from=derived_from or [],
                metadata=metadata or {},
            )
            spans.append(span)
            return span

        if source.user_message is not None:
            user_text = _message_text(source.user_message.content)
            append_span(
                source_ref=f"message:{source.user_message.id}",
                kind="user_correction" if _CORRECTION_RE.search(user_text) else "user_goal",
                provenance="user",
                text=user_text,
            )

        assistant_content = source.assistant_message.content
        parts = assistant_content.get("parts") if isinstance(assistant_content, dict) else []
        memory_context = (
            run.memory_context if isinstance(getattr(run, "memory_context", None), dict) else {}
        )
        recalled: list[str] = [
            str(value) for value in memory_context.get("memory_ids") or [] if value
        ]
        external_spans: list[str] = []
        recall_active = bool(recalled)
        for index, part in enumerate(parts if isinstance(parts, list) else []):
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "")
            if part_type in {"reasoning", "system"}:
                continue
            if part_type == "retrieval":
                recalled.extend(_memory_ids(part))
                recall_active = True
                continue
            if part_type == "tool":
                if str(part.get("name") or "") in {"search_memory", "get_memory_source"}:
                    recalled.extend(_memory_tool_ids(part))
                    recall_active = True
                    continue
                state = str(part.get("state") or part.get("status") or "").casefold()
                if state not in _TERMINAL_TOOL_STATES:
                    continue
                provenance = _tool_provenance(part)
                kind = _tool_kind(part)
                outcome = part.get("error") or part.get("output") or part.get("outcome")
                span = append_span(
                    source_ref=f"tool:{part.get('tool_call_id') or index}",
                    kind=kind,
                    provenance=provenance,
                    text=outcome,
                    limit=_MAX_TOOL_TEXT,
                    metadata={
                        "tool_name": str(part.get("name") or "tool")[:160],
                        "state": state,
                        "outcome": str(part.get("outcome") or "")[:80],
                        "error_category": str(part.get("errorCategory") or "")[:80],
                        "exit_code": part.get("exit_code"),
                        "timed_out": bool(part.get("timed_out")),
                        "truncated": bool(part.get("truncated")),
                        "logical_path": _logical_path(part) if kind == "artifact" else None,
                    },
                )
                if span is not None and provenance == "tool_external":
                    external_spans.append(span.id)
                continue
            if part_type in {"artifact", "file"}:
                span = append_span(
                    source_ref=f"artifact:{part.get('artifact_id') or part.get('id') or index}",
                    kind="artifact",
                    provenance="tool_internal",
                    text=part.get("summary") or part.get("content") or part.get("status"),
                    limit=_MAX_TOOL_TEXT,
                    metadata={
                        "logical_path": _logical_path(part),
                        "content_digest": str(part.get("digest") or "")[:64],
                        "size": part.get("size"),
                        "status": str(part.get("status") or "")[:80],
                    },
                )
                continue
            if part_type == "text":
                text = part.get("content") or part.get("text")
                if recall_active:
                    continue
                append_span(
                    source_ref=f"message:{source.assistant_message.id}#text:{index}",
                    kind="assistant_conclusion",
                    provenance="assistant_derived",
                    effective_provenance="tool_external" if external_spans else "assistant_derived",
                    derived_from=sorted(set(external_spans)),
                    text=text,
                )

        persisted = run.snapshot if isinstance(run.snapshot, dict) else {}
        compaction = persisted.get("_compaction")
        if isinstance(compaction, dict) and compaction.get("summary"):
            append_span(
                source_ref=f"chunk:compaction:{run.id}",
                kind="compaction",
                provenance="assistant_derived",
                text=compaction["summary"],
            )

        canonical = {
            "schema_version": "run-memory-snapshot-v1",
            "run_id": str(run.id),
            "user_id": str(run.user_id),
            "session_id": str(run.session_id),
            "scope_key": scope_key,
            "source_watermark": int(run.updated_at),
            "spans": [span.model_dump(mode="json") for span in spans],
            "recalled_memory_ids": sorted(set(recalled)),
            "compaction_covered": isinstance(compaction, dict),
        }
        encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        canonical["content_digest"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        canonical["token_estimate"] = (len(encoded) + 3) // 4
        return RunSnapshotPayload.model_validate(canonical)


__all__ = ["RunSnapshotBuilder"]
