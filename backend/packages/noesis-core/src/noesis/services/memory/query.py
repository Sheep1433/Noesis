"""Bounded read-only explicit memory query with evidence-first output."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.config.env import MachineMemoryConfig
from noesis.repositories.machine_memory_repository import MachineMemoryRepository
from noesis.schemas.memory import MemoryDeepQueryItem, MemoryDeepQueryResponse
from noesis.services.memory.bulletin import merged_score, render_bulletin
from noesis.services.memory.index import MemoryIndexService
from noesis.services.memory.manifest import search_manifest_handles
from noesis.services.memory.source import MemorySourceService
from noesis.runtime.logging import logger
from noesis.storage.postgres.manager import pg_manager
from noesis.storage.postgres.models.memory import TMemoryQueryTrace


_QUERY_SLOTS = asyncio.Semaphore(MachineMemoryConfig.deep_query_concurrency)


@dataclass(frozen=True)
class _QueryExecution:
    result: MemoryDeepQueryResponse
    steps: int


def _unavailable(
    error: str | None, *, evidence_status: str = "unavailable"
) -> MemoryDeepQueryResponse:
    return MemoryDeepQueryResponse(
        bulletin="",
        memory_ids=[],
        source_spans=[],
        evidence_status=evidence_status,
        error=error,
    )


async def _record_query_trace(
    *,
    user_id: str,
    scope_key: str,
    duration_ms: int,
    result: MemoryDeepQueryResponse,
    input_tokens: int,
    steps: int,
) -> None:
    try:
        async with pg_manager.get_async_session_context() as trace_db:
            trace_db.add(TMemoryQueryTrace(
                user_id=str(user_id),
                scope_key=scope_key,
                duration_ms=duration_ms,
                steps=steps,
                returned_spans=len(result.source_spans),
                input_tokens=input_tokens,
                output_tokens=(len(result.bulletin) + 3) // 4,
                evidence_status=result.evidence_status,
                failure_category=(
                    "timeout"
                    if result.error and "超时" in result.error
                    else "dependency"
                    if result.evidence_status == "unavailable"
                    else None
                ),
            ))
            await trace_db.commit()
    except Exception:
        logger.warning("memory query trace persistence failed")


class MemoryQueryService:
    @staticmethod
    def search_manifest(
        *, user_id: str, scope_key: str, query: str, limit: int = 20
    ) -> list[str]:
        return search_manifest_handles(
            user_id=str(user_id), scope_key=scope_key, query=query, limit=limit
        )

    @staticmethod
    async def search_memory_items(
        db: AsyncSession,
        *,
        user_id: str,
        scope_key: str,
        query: str,
        statuses: tuple[str, ...] = ("active", "needs_review"),
        limit: int = 20,
    ) -> list[str]:
        items = await MachineMemoryRepository(db).search_items(
            user_id=str(user_id),
            scope_key=scope_key,
            query=query,
            statuses=statuses,
            limit=limit,
        )
        return [item.id for item in items]

    @staticmethod
    async def read_run_span(
        db: AsyncSession,
        *,
        user_id: str,
        memory_id: str,
        evidence_id: str,
        scope_key: str | None = None,
    ):
        return await MemorySourceService.get(
            db,
            user_id=str(user_id),
            memory_id=memory_id,
            evidence_id=evidence_id,
            scope_key=scope_key,
        )

    @staticmethod
    async def read_artifact_summary(
        db: AsyncSession,
        *,
        user_id: str,
        memory_id: str,
        evidence_id: str,
        scope_key: str | None = None,
    ):
        source = await MemoryQueryService.read_run_span(
            db,
            user_id=user_id,
            memory_id=memory_id,
            evidence_id=evidence_id,
            scope_key=scope_key,
        )
        if source.source_kind != "artifact":
            raise LookupError("来源不是任务产物")
        return source

    @staticmethod
    async def search(
        db: AsyncSession,
        *,
        user_id: str,
        scope_key: str,
        query: str,
        memory_types: tuple[str, ...] = (),
        include_history: bool = False,
        statuses: tuple[str, ...] = (),
        source_types: tuple[str, ...] = (),
        expand_evidence: bool = True,
        since: datetime | None = None,
        until: datetime | None = None,
        top_k: int = 5,
        index: MemoryIndexService | None = None,
        record_trace: bool = True,
    ) -> MemoryDeepQueryResponse:
        started = time.perf_counter()
        try:
            async with _QUERY_SLOTS:
                execution = await asyncio.wait_for(
                    MemoryQueryService._search(
                        db,
                        user_id=user_id,
                        scope_key=scope_key,
                        query=query,
                        memory_types=memory_types,
                        include_history=include_history,
                        statuses=statuses,
                        source_types=source_types,
                        expand_evidence=expand_evidence,
                        since=since,
                        until=until,
                        top_k=top_k,
                        index=index,
                    ),
                    timeout=MachineMemoryConfig.deep_query_timeout_seconds,
                )
                result = execution.result
                steps = execution.steps
        except TimeoutError:
            result = _unavailable("记忆搜索超时，请缩小问题范围后重试。")
            steps = 0
        duration_ms = round((time.perf_counter() - started) * 1000)
        logger.info(
            "memory query finished status={} duration_ms={} steps={} spans={} items={}",
            result.evidence_status,
            duration_ms,
            steps,
            len(result.source_spans),
            len(result.memory_ids),
        )
        if record_trace:
            try:
                await asyncio.wait_for(
                    _record_query_trace(
                        user_id=user_id,
                        scope_key=scope_key,
                        duration_ms=duration_ms,
                        result=result,
                        input_tokens=(len(query) + 3) // 4,
                        steps=steps,
                    ),
                    timeout=min(
                        1.0, MachineMemoryConfig.deep_query_timeout_seconds * 0.2
                    ),
                )
            except TimeoutError:
                logger.warning("memory query trace persistence timed out")
        return result

    @staticmethod
    async def _search(
        db: AsyncSession,
        *,
        user_id: str,
        scope_key: str,
        query: str,
        memory_types: tuple[str, ...] = (),
        include_history: bool = False,
        statuses: tuple[str, ...] = (),
        source_types: tuple[str, ...] = (),
        expand_evidence: bool = True,
        since: datetime | None = None,
        until: datetime | None = None,
        top_k: int = 5,
        index: MemoryIndexService | None = None,
    ) -> _QueryExecution:
        required_steps = 3 + int(bool(source_types)) + int(expand_evidence)
        if MachineMemoryConfig.deep_query_max_steps < required_steps:
            return _QueryExecution(
                _unavailable("记忆搜索步骤预算不足。"), 0
            )
        requested_statuses = statuses or (
            ("candidate", "active", "needs_review", "superseded", "disabled", "invalidated")
            if include_history
            else ("active",)
        )
        query_statuses = (
            requested_statuses
            if include_history
            else tuple(dict.fromkeys((*requested_statuses, "needs_review")))
        )
        repository = MachineMemoryRepository(db)
        steps = 0
        try:
            lexical = await repository.search_items(
                user_id=str(user_id),
                scope_key=scope_key,
                query=query,
                statuses=query_statuses,
                memory_types=memory_types,
                since=since,
                until=until,
                limit=min(30, top_k * 3),
            )
            steps += 1
        except Exception as exc:
            logger.warning(
                "memory query stage failed stage=lexical failure_category={}",
                type(exc).__name__,
            )
            return _QueryExecution(
                _unavailable("记忆搜索暂不可用，请稍后重试。"), steps + 1
            )
        conflict_match = any(item.status == "needs_review" for item in lexical)
        if not include_history:
            lexical = [item for item in lexical if item.status == "active"]
        semantic_error = False
        try:
            semantic = await asyncio.wait_for(
                (index or MemoryIndexService()).search(
                query=query,
                user_id=str(user_id),
                scope_key=scope_key,
                limit=min(30, top_k * 3),
                ),
                timeout=MachineMemoryConfig.deep_query_timeout_seconds * 0.6,
            )
            steps += 1
        except Exception as exc:
            logger.warning(
                "memory query stage failed stage=semantic failure_category={}",
                type(exc).__name__,
            )
            semantic = []
            semantic_error = True
            steps += 1
        semantic_scores = dict(semantic)
        by_id = {item.id: item for item in lexical}
        if semantic_scores and "active" in requested_statuses:
            try:
                semantic_items = await repository.eligible_items_by_ids(
                    user_id=str(user_id),
                    scope_key=scope_key,
                    memory_ids=list(semantic_scores),
                )
            except Exception as exc:
                logger.warning(
                    "memory query stage failed stage=eligibility failure_category={}",
                    type(exc).__name__,
                )
                return _QueryExecution(
                    _unavailable("记忆证据校验暂不可用。"), steps + 1
                )
            steps += 1
            if since is not None:
                semantic_items = [
                    item
                    for item in semantic_items
                    if item.last_verified_at is not None
                    and item.last_verified_at >= since
                ]
            if until is not None:
                semantic_items = [
                    item
                    for item in semantic_items
                    if item.last_verified_at is not None
                    and item.last_verified_at <= until
                ]
            by_id.update({item.id: item for item in semantic_items})
        ranked = sorted(
            (
                (item, score)
                for item in by_id.values()
                if (
                    score := merged_score(query, item, semantic_scores)
                ) >= MachineMemoryConfig.retrieval_min_score
            ),
            key=lambda pair: (-pair[1], pair[0].memory_type, pair[0].id),
        )[:top_k]
        if not ranked:
            if conflict_match:
                return _QueryExecution(
                    _unavailable(
                        "相关历史存在待确认的冲突证据。",
                        evidence_status="contradicts",
                    ),
                    steps,
                )
            return _QueryExecution(
                MemoryDeepQueryResponse(
                    bulletin="No supported memory evidence was found.",
                    memory_ids=[],
                    source_spans=[],
                    evidence_status=(
                        "unavailable" if semantic_error else "insufficient"
                    ),
                    error="部分记忆索引暂不可用。" if semantic_error else None,
                ),
                steps,
            )
        ids = [item.id for item, _ in ranked]
        source_types_by_memory: dict[str, set[str]] = {}
        if source_types:
            try:
                source_types_by_memory = await repository.source_types_for_items(
                    user_id=str(user_id),
                    memory_ids=ids,
                    source_types=source_types,
                )
            except Exception as exc:
                logger.warning(
                    "memory query stage failed stage=source_types failure_category={}",
                    type(exc).__name__,
                )
                return _QueryExecution(
                    _unavailable("记忆来源类型校验暂不可用。"), steps + 1
                )
            steps += 1
            ranked = [
                pair for pair in ranked if pair[0].id in source_types_by_memory
            ]
            ids = [item.id for item, _ in ranked]
            if not ranked:
                return _QueryExecution(
                    _unavailable(None, evidence_status="insufficient"), steps
                )
        evidence = []
        if expand_evidence:
            try:
                evidence = await repository.list_evidence_for_items(
                    user_id=str(user_id),
                    memory_ids=ids,
                    source_types=source_types,
                    limit=MachineMemoryConfig.deep_query_max_spans,
                )
            except Exception as exc:
                logger.warning(
                    "memory query stage failed stage=evidence failure_category={}",
                    type(exc).__name__,
                )
                return _QueryExecution(
                    _unavailable("记忆来源暂不可用。"), steps + 1
                )
            steps += 1
        source_spans = [
            f"{item.memory_id}:{item.id}:{item.source_kind}"
            for item in evidence
        ] if expand_evidence else []
        has_review = conflict_match or any(
            item.status == "needs_review" for item, _ in ranked
        )
        best_score = ranked[0][1]
        status = "contradicts" if has_review else "exact" if best_score >= 0.8 else "near"
        bulletin = render_bulletin(
            ranked, max_tokens=MachineMemoryConfig.bulletin_max_tokens
        )
        for value in evidence:
            source_types_by_memory.setdefault(value.memory_id, set()).add(
                value.source_kind
            )
        return _QueryExecution(MemoryDeepQueryResponse(
            bulletin=bulletin.text,
            memory_ids=ids,
            source_spans=source_spans,
            evidence_status=status,
            items=[
                MemoryDeepQueryItem(
                    memory_id=item.id,
                    memory_type=item.memory_type,
                    status=item.status,
                    score=max(0.0, min(1.0, score)),
                    source_types=sorted(source_types_by_memory.get(item.id, set())),
                )
                for item, score in ranked
            ],
            error=(
                "语义索引暂不可用，已返回文本检索结果。"
                if semantic_error
                else None
            ),
        ), steps)


__all__ = ["MemoryQueryService"]
