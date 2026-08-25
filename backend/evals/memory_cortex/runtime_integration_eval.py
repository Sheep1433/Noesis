"""Production root-Agent paired A/B and prompt-cache integration evaluation."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.tools import StructuredTool
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from sqlalchemy import delete

from noesis.agents.middlewares.dynamic_context_middleware import DynamicContextBlock
from noesis.agents.middlewares.memory_bulletin_middleware import (
    MemoryBulletinMiddleware,
)
from noesis.chat.event_mapping.usage_normalize import normalize_usage
from noesis.config import memory_paths
from noesis.config.env import MachineMemoryConfig, ModelConfig
from noesis.factory import create_noesis_agent
from noesis.knowledge.runtime import (
    close_knowledge_base,
    init_knowledge_base,
    knowledge_base,
)
from noesis.llm.factory import get_llm
from noesis.repositories.memory_preference_repository import MemoryPreferenceRepository
from noesis.services.memory.bulletin import MemoryBulletin, MemoryBulletinService
from noesis.services.memory.index import MemoryIndexService
from noesis.services.memory.query import MemoryQueryService
from noesis.services.memory.workspace import MemoryWorkspaceService
from noesis.storage.postgres.manager import pg_manager
from noesis.storage.postgres.models.memory import (
    TMemoryEvidence,
    TMemoryItem,
    TMemoryQueryTrace,
    TMemoryUserPreference,
)

from evals.memory_cortex.metrics import contains_concepts, paired_delta
from evals.memory_cortex.report_meta import (
    effective_config_snapshot,
    evaluation_fingerprint,
)


ROOT = Path(__file__).parent
EVAL_CONFIG = json.loads((ROOT / "eval_config.json").read_text(encoding="utf-8"))
ON_USER = "00000000-0000-0000-0000-000000000301"
OFF_USER = "00000000-0000-0000-0000-000000000302"
SCOPE = "profile:MEMORY_EVAL|project:runtime-integration"
TEMP_COLLECTION = "noesis_memory_runtime_integration_20260824"
SYSTEM_PROMPT = (
    "Answer the project task concisely. Use applicable scoped task memory as evidence "
    "when available. If project-specific evidence is unavailable, give the best answer "
    "you can and state uncertainty instead of inventing project decisions."
)
CACHE_SYSTEM_PROMPT = (
    "Stable project instructions for prompt-cache integration. " * 700
    + " Follow the current user request and use tools exactly when requested."
)


def _stable_id(value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"noesis-memory-runtime:{value}"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _monetary_cost() -> float | None:
    return 0.0 if "free" in ModelConfig.model_name.casefold() else None


def _item(task: dict, *, user_id: str) -> TMemoryItem:
    return TMemoryItem(
        id=_stable_id(f"{user_id}:{task['id']}"),
        user_id=user_id,
        scope_key=SCOPE,
        memory_type=task["type"],
        subject=task["subject"],
        subject_key=_digest(task["id"]),
        statement=task["statement"],
        applicability=task["applicability"],
        content_digest=_digest(task["statement"]),
        effective_provenance="user",
        status="active",
        version=1,
        last_verified_at=datetime.now(timezone.utc),
    )


def _evidence(item: TMemoryItem, task_id: str) -> TMemoryEvidence:
    return TMemoryEvidence(
        id=_stable_id(f"evidence:{item.user_id}:{task_id}"),
        memory_id=item.id,
        snapshot_id=None,
        user_id=str(item.user_id),
        run_id=None,
        source_kind="message",
        source_ref=f"runtime-integration:{task_id}",
        span_digest=_digest(f"span:{task_id}"),
        provenance="user",
        excerpt=item.statement,
    )


async def _reset_users(db) -> None:
    users = (ON_USER, OFF_USER)
    await db.execute(
        delete(TMemoryQueryTrace).where(TMemoryQueryTrace.user_id.in_(users))
    )
    await db.execute(delete(TMemoryItem).where(TMemoryItem.user_id.in_(users)))
    await db.execute(
        delete(TMemoryUserPreference).where(TMemoryUserPreference.user_id.in_(users))
    )
    await db.commit()


async def _seed(db, fixture: dict) -> tuple[list[TMemoryItem], list[TMemoryItem]]:
    on_items = [_item(task, user_id=ON_USER) for task in fixture["tasks"]]
    off_items = [_item(task, user_id=OFF_USER) for task in fixture["tasks"]]
    db.add_all([*on_items, *off_items])
    await db.flush()
    for task, on_item, off_item in zip(
        fixture["tasks"], on_items, off_items, strict=True
    ):
        db.add(_evidence(on_item, task["id"]))
        db.add(_evidence(off_item, task["id"]))
    await MemoryPreferenceRepository(db).set(user_id=ON_USER, enabled=True)
    await MemoryPreferenceRepository(db).set(user_id=OFF_USER, enabled=False)
    await db.commit()
    return on_items, off_items


def _message_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(block.get("text") or "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


@dataclass
class AgentRun:
    text: str
    model_calls: list[dict]
    tool_calls: int
    failure_category: str | None = None
    prompt_observations: list[dict] | None = None


class PromptProbe(AgentMiddleware):
    def __init__(self) -> None:
        self.observations: list[dict] = []

    def _observe(self, request: ModelRequest[ContextT]) -> None:
        system = request.system_message
        stable = json.dumps(
            system.content if system is not None else None,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        bulletin_positions = [
            index
            for index, message in enumerate(request.messages)
            if message.additional_kwargs.get("noesis_late_context") == "memory-bulletin"
        ]
        self.observations.append(
            {
                "stable_prefix_hash": _digest(stable),
                "bulletin_positions": bulletin_positions,
                "bulletin_after_stable_prefix": system is not None
                and bool(bulletin_positions),
            }
        )

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        self._observe(request)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[
            [ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]
        ],
    ) -> ModelResponse[ResponseT]:
        self._observe(request)
        return await handler(request)


async def _run_agent(
    *,
    model,
    middleware: MemoryBulletinMiddleware,
    prompt: str,
    thread_id: str,
    system_prompt: str = SYSTEM_PROMPT,
    tools=(),
    seed: int,
) -> AgentRun:
    fixed_context = DynamicContextBlock(
        current_time="2026-08-24T17+08:00",
        timezone="Asia/Shanghai",
        workspace="runtime-integration",
        session_id="runtime-integration",
    )
    prompt_probe = PromptProbe()
    seeded_model = model.bind(seed=seed)
    agent = create_noesis_agent(
        system_prompt=system_prompt,
        checkpointer=None,
        profile="MEMORY_EVAL",
        tools=tools,
        dynamic_context_provider=lambda: fixed_context,
        memory_bulletin_middleware=middleware,
        middleware=(prompt_probe,),
        session_id="runtime-integration",
        model=seeded_model,
    )
    text_parts: list[str] = []
    model_calls: list[dict] = []
    tool_call_ids: set[str] = set()
    call_started = time.perf_counter()
    first_chunk_at: float | None = None
    try:
        async for chunk, _metadata in agent.astream(
            {"messages": [HumanMessage(content=prompt)]},
            config={"configurable": {"thread_id": thread_id}},
            stream_mode="messages",
        ):
            if not isinstance(chunk, AIMessageChunk):
                continue
            if first_chunk_at is None:
                first_chunk_at = time.perf_counter()
            text_parts.append(_message_text(chunk.content))
            for tool_call in chunk.tool_calls:
                tool_id = tool_call.get("id")
                if tool_id:
                    tool_call_ids.add(tool_id)
            if chunk.usage_metadata:
                usage = normalize_usage(chunk.usage_metadata)
                details = usage.get("input_token_details") or {}
                model_calls.append(
                    {
                        "cache_read_tokens": details.get("cache_read"),
                        "cache_write_tokens": details.get("cache_write"),
                        "uncached_input_tokens": usage.get("uncached_input_tokens"),
                        "input_tokens": usage.get("input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                        "cache_metrics_available": usage.get("cache_metrics_available"),
                        "ttft_ms": round(
                            ((first_chunk_at or time.perf_counter()) - call_started)
                            * 1000,
                            3,
                        ),
                        "failure_category": None,
                    }
                )
                call_started = time.perf_counter()
                first_chunk_at = None
    except Exception as exc:
        return AgentRun(
            text="".join(text_parts),
            model_calls=model_calls
            or [
                {
                    "cache_read_tokens": None,
                    "cache_write_tokens": None,
                    "uncached_input_tokens": None,
                    "input_tokens": None,
                    "output_tokens": None,
                    "cache_metrics_available": False,
                    "ttft_ms": None,
                    "failure_category": type(exc).__name__,
                }
            ],
            tool_calls=len(tool_call_ids),
            failure_category=type(exc).__name__,
            prompt_observations=prompt_probe.observations,
        )
    return AgentRun(
        "".join(text_parts),
        model_calls,
        len(tool_call_ids),
        prompt_observations=prompt_probe.observations,
    )


class BulletinProbe:
    def __init__(self, db, *, user_id: str, index: MemoryIndexService):
        self.db = db
        self.user_id = user_id
        self.index = index
        self.bulletins: list[MemoryBulletin] = []
        self.latencies_ms: list[float] = []

    async def __call__(self, query: str) -> MemoryBulletin:
        started = time.perf_counter()
        bulletin = await MemoryBulletinService.build(
            self.db,
            user_id=self.user_id,
            scope_key=SCOPE,
            query=query,
            index=self.index,
        )
        self.latencies_ms.append((time.perf_counter() - started) * 1000)
        self.bulletins.append(bulletin)
        return bulletin


def _run_metrics(run: AgentRun) -> dict[str, int | float]:
    input_tokens = sum(int(call.get("input_tokens") or 0) for call in run.model_calls)
    output_tokens = sum(int(call.get("output_tokens") or 0) for call in run.model_calls)
    cache_read = sum(
        int(call.get("cache_read_tokens") or 0) for call in run.model_calls
    )
    cache_write = sum(
        int(call.get("cache_write_tokens") or 0) for call in run.model_calls
    )
    return {
        "tokens": input_tokens + output_tokens,
        "ttft_ms": next(
            (
                float(call["ttft_ms"])
                for call in run.model_calls
                if call.get("ttft_ms") is not None
            ),
            0.0,
        ),
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "uncached_input_tokens": sum(
            int(call.get("uncached_input_tokens") or 0) for call in run.model_calls
        ),
        "cache_read_ratio": cache_read / input_tokens if input_tokens else 0.0,
    }


async def _paired_report(db, fixture: dict, index: MemoryIndexService, model) -> dict:
    observations = []
    query_latencies = []
    for position, task in enumerate(fixture["tasks"]):
        on_probe = BulletinProbe(db, user_id=ON_USER, index=index)
        off_probe = BulletinProbe(db, user_id=OFF_USER, index=index)
        on_run = await _run_agent(
            model=model,
            middleware=MemoryBulletinMiddleware(
                run_id=f"paired-on-{task['id']}", provider=on_probe
            ),
            prompt=task["query"],
            thread_id=f"paired-on-{task['id']}",
            seed=EVAL_CONFIG["seed"] + position,
        )
        off_run = await _run_agent(
            model=model,
            middleware=MemoryBulletinMiddleware(
                run_id=f"paired-off-{task['id']}", provider=off_probe
            ),
            prompt=task["query"],
            thread_id=f"paired-off-{task['id']}",
            seed=EVAL_CONFIG["seed"] + position,
        )
        criteria = list(task["success_criteria"])
        on_matched = [
            criterion
            for criterion in criteria
            if contains_concepts(criterion, on_run.text)
        ]
        off_matched = [
            criterion
            for criterion in criteria
            if contains_concepts(criterion, off_run.text)
        ]
        on_metrics = _run_metrics(on_run)
        off_metrics = _run_metrics(off_run)
        query_latencies.extend(on_probe.latencies_ms)
        on_bulletin = (
            on_probe.bulletins[0] if on_probe.bulletins else MemoryBulletin("", "", ())
        )
        off_bulletin = (
            off_probe.bulletins[0]
            if off_probe.bulletins
            else MemoryBulletin("", "", ())
        )
        observations.append(
            {
                "task_id": task["id"],
                "expected_memory_id": _stable_id(f"{ON_USER}:{task['id']}"),
                "paired_seed": EVAL_CONFIG["seed"] + position,
                "model_seed_applied": True,
                "on_user_id": ON_USER,
                "off_user_id": OFF_USER,
                "memory_on": {
                    "success": len(on_matched) == len(criteria),
                    "matched_criteria": on_matched,
                    **on_metrics,
                    "tool_calls": on_run.tool_calls,
                    "answer_digest": _digest(on_run.text),
                    "failure_category": on_run.failure_category,
                    "bulletin_memory_ids": list(on_bulletin.memory_ids),
                    "bulletin_hash": on_bulletin.bulletin_hash,
                    "bulletin_degraded": on_bulletin.degraded,
                },
                "memory_off": {
                    "success": len(off_matched) == len(criteria),
                    "matched_criteria": off_matched,
                    **off_metrics,
                    "tool_calls": off_run.tool_calls,
                    "answer_digest": _digest(off_run.text),
                    "failure_category": off_run.failure_category,
                    "bulletin_memory_ids": list(off_bulletin.memory_ids),
                    "bulletin_hash": off_bulletin.bulletin_hash,
                    "bulletin_degraded": off_bulletin.degraded,
                },
            }
        )
    off_success = [float(item["memory_off"]["success"]) for item in observations]
    on_success = [float(item["memory_on"]["success"]) for item in observations]
    success_delta = paired_delta(off_success, on_success, seed=20260824)
    snapshot_payload = [
        {
            key: task[key]
            for key in ("id", "type", "subject", "statement", "applicability")
        }
        for task in fixture["tasks"]
    ]
    snapshot_digest = _digest(json.dumps(snapshot_payload, sort_keys=True))
    metrics = {
        "memory_off_task_success_rate": statistics.mean(off_success),
        "memory_on_task_success_rate": statistics.mean(on_success),
        "task_success_delta": asdict(success_delta),
        "repeated_failure_reduction": {
            "measurement_status": "not_measured",
            "reason": "This frozen task set measures task success, not repeated executions of a prior failure.",
        },
        "memory_off_tokens": sum(item["memory_off"]["tokens"] for item in observations),
        "memory_on_tokens": sum(item["memory_on"]["tokens"] for item in observations),
        "memory_off_cache_read_tokens": sum(
            item["memory_off"]["cache_read_tokens"] for item in observations
        ),
        "memory_on_cache_read_tokens": sum(
            item["memory_on"]["cache_read_tokens"] for item in observations
        ),
        "memory_off_cache_write_tokens": sum(
            item["memory_off"]["cache_write_tokens"] for item in observations
        ),
        "memory_on_cache_write_tokens": sum(
            item["memory_on"]["cache_write_tokens"] for item in observations
        ),
        "memory_off_uncached_input_tokens": sum(
            item["memory_off"]["uncached_input_tokens"] for item in observations
        ),
        "memory_on_uncached_input_tokens": sum(
            item["memory_on"]["uncached_input_tokens"] for item in observations
        ),
        "memory_off_cache_read_ratio": statistics.mean(
            item["memory_off"]["cache_read_ratio"] for item in observations
        ),
        "memory_on_cache_read_ratio": statistics.mean(
            item["memory_on"]["cache_read_ratio"] for item in observations
        ),
        "memory_on_bulletin_degraded_rate": statistics.mean(
            float(item["memory_on"]["bulletin_degraded"]) for item in observations
        ),
        "memory_on_expected_recall_rate": statistics.mean(
            float(
                item["expected_memory_id"] in item["memory_on"]["bulletin_memory_ids"]
            )
            for item in observations
        ),
        "memory_off_mean_ttft_ms": statistics.mean(
            item["memory_off"]["ttft_ms"] for item in observations
        ),
        "memory_on_mean_ttft_ms": statistics.mean(
            item["memory_on"]["ttft_ms"] for item in observations
        ),
        "memory_query_latency_ms": statistics.mean(query_latencies),
        "background_extraction": {
            "measurement_status": "paused_by_design",
            "input_tokens": 0,
            "output_tokens": 0,
            "reason": "Automatic capture and extraction are paused for both paired groups.",
        },
        "monetary_cost": _monetary_cost(),
        "memory_off_tool_calls": sum(
            item["memory_off"]["tool_calls"] for item in observations
        ),
        "memory_on_tool_calls": sum(
            item["memory_on"]["tool_calls"] for item in observations
        ),
    }
    threshold = -0.02
    gate_passed = bool(
        all(item["memory_on"]["failure_category"] is None for item in observations)
        and all(item["memory_off"]["failure_category"] is None for item in observations)
        and success_delta.ci95_low >= threshold
        and success_delta.ci95_low > 0
    )
    return {
        "report_version": "memory-paired-integration-v1",
        "mode": "production_integration",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_fingerprint": evaluation_fingerprint(),
        "effective_config": effective_config_snapshot(),
        "fixture_revision": fixture["revision"],
        "seed": 20260824,
        "runtime_model": ModelConfig.model_name,
        "runtime_max_output_tokens": EVAL_CONFIG["paired_max_output_tokens"],
        "on_user_id": ON_USER,
        "off_user_id": OFF_USER,
        "on_snapshot_digest": snapshot_digest,
        "off_snapshot_digest": snapshot_digest,
        "automatic_capture_paused": True,
        "task_count": len(observations),
        "observations": observations,
        "metrics": metrics,
        "gate_passed": gate_passed,
    }


def _cache_metrics(scenarios: dict) -> tuple[dict, int, int]:
    calls = [call for scenario in scenarios.values() for call in scenario["calls"]]
    failures = [call for call in calls if call.get("failure_category")]
    read_available = [
        call for call in calls if call.get("cache_read_tokens") is not None
    ]
    ttft_available = [call for call in calls if call.get("ttft_ms") is not None]
    return (
        {
            "cache_read_availability": len(read_available) / len(calls) if calls else 0,
            "cache_write_availability": sum(
                call.get("cache_write_tokens") is not None for call in calls
            )
            / len(calls)
            if calls
            else 0,
            "cache_read_tokens": sum(
                call.get("cache_read_tokens") or 0 for call in calls
            ),
            "cache_write_tokens": sum(
                call.get("cache_write_tokens") or 0 for call in calls
            ),
            "uncached_input_tokens": sum(
                call.get("uncached_input_tokens") or 0 for call in calls
            ),
            "ttft_availability": len(ttft_available) / len(calls) if calls else 0,
            "mean_ttft_ms": statistics.mean(call["ttft_ms"] for call in ttft_available)
            if ttft_available
            else None,
            "monetary_cost": _monetary_cost(),
        },
        len(calls),
        len(failures),
    )


async def _cache_report(db, fixture: dict, index: MemoryIndexService, model) -> dict:
    task = fixture["tasks"][0]

    async def cache_step(value: str) -> str:
        """Return a deterministic cache integration marker."""
        return f"cache-step:{value}"

    cache_tool = StructuredTool.from_function(
        coroutine=cache_step,
        name="cache_step",
        description="Return a deterministic marker for cache integration.",
    )

    same_probe = BulletinProbe(db, user_id=ON_USER, index=index)
    same_run = await _run_agent(
        model=model,
        middleware=MemoryBulletinMiddleware(
            run_id="cache-same-run", provider=same_probe
        ),
        prompt=(
            "For the memory enablement control task, call cache_step exactly once with "
            "value probe, then reply DONE."
        ),
        thread_id="cache-same-run",
        system_prompt=CACHE_SYSTEM_PROMPT,
        tools=(cache_tool,),
        seed=EVAL_CONFIG["seed"] + 100,
    )

    same_a_probe = BulletinProbe(db, user_id=ON_USER, index=index)
    same_b_probe = BulletinProbe(db, user_id=ON_USER, index=index)
    same_a = await _run_agent(
        model=model,
        middleware=MemoryBulletinMiddleware(
            run_id="cache-new-same-a", provider=same_a_probe
        ),
        prompt=task["query"],
        thread_id="cache-new-same-a",
        system_prompt=CACHE_SYSTEM_PROMPT,
        seed=EVAL_CONFIG["seed"] + 101,
    )
    same_b = await _run_agent(
        model=model,
        middleware=MemoryBulletinMiddleware(
            run_id="cache-new-same-b", provider=same_b_probe
        ),
        prompt=task["query"],
        thread_id="cache-new-same-b",
        system_prompt=CACHE_SYSTEM_PROMPT,
        seed=EVAL_CONFIG["seed"] + 101,
    )

    changed_a_probe = BulletinProbe(db, user_id=ON_USER, index=index)
    changed_a = await _run_agent(
        model=model,
        middleware=MemoryBulletinMiddleware(
            run_id="cache-changed-a", provider=changed_a_probe
        ),
        prompt=task["query"],
        thread_id="cache-changed-a",
        system_prompt=CACHE_SYSTEM_PROMPT,
        seed=EVAL_CONFIG["seed"] + 102,
    )
    changed_item = await db.get(TMemoryItem, _stable_id(f"{ON_USER}:{task['id']}"))
    changed_item.statement = (
        "Use one user-controlled memory switch, with a revised settings explanation."
    )
    changed_item.content_digest = _digest(changed_item.statement)
    changed_item.version += 1
    await db.commit()
    await index.sync_item(
        db, user_id=ON_USER, scope_key=SCOPE, memory_id=changed_item.id
    )
    changed_b_probe = BulletinProbe(db, user_id=ON_USER, index=index)
    changed_b = await _run_agent(
        model=model,
        middleware=MemoryBulletinMiddleware(
            run_id="cache-changed-b", provider=changed_b_probe
        ),
        prompt=task["query"],
        thread_id="cache-changed-b",
        system_prompt=CACHE_SYSTEM_PROMPT,
        seed=EVAL_CONFIG["seed"] + 102,
    )

    async def search_memory(query: str) -> str:
        """Search scoped machine memory and return evidence-first JSON."""
        result = await MemoryQueryService.search(
            db,
            user_id=ON_USER,
            scope_key=SCOPE,
            query=query,
            index=index,
            record_trace=False,
        )
        return result.model_dump_json()

    memory_tool = StructuredTool.from_function(
        coroutine=search_memory,
        name="search_memory",
        description="Search scoped machine memory with source evidence.",
    )
    deep_probe = BulletinProbe(db, user_id=ON_USER, index=index)
    deep_run = await _run_agent(
        model=model,
        middleware=MemoryBulletinMiddleware(
            run_id="cache-deep-query", provider=deep_probe
        ),
        prompt=(
            "Call search_memory exactly once with query 'memory enablement control', "
            "then summarize the control policy."
        ),
        thread_id="cache-deep-query",
        system_prompt=CACHE_SYSTEM_PROMPT,
        tools=(memory_tool,),
        seed=EVAL_CONFIG["seed"] + 103,
    )

    old = (
        changed_a_probe.bulletins[0]
        if changed_a_probe.bulletins
        else MemoryBulletin("", "", ())
    )
    changed = (
        changed_b_probe.bulletins[0]
        if changed_b_probe.bulletins
        else MemoryBulletin("", "", ())
    )
    same_first = (
        same_a_probe.bulletins[0]
        if same_a_probe.bulletins
        else MemoryBulletin("", "", ())
    )
    same_second = (
        same_b_probe.bulletins[0]
        if same_b_probe.bulletins
        else MemoryBulletin("", "", ())
    )
    changed_a_prompt = (changed_a.prompt_observations or [{}])[0]
    changed_b_prompt = (changed_b.prompt_observations or [{}])[0]
    scenarios = {
        "same_run": {
            "bulletin_hash_equal": len(same_probe.bulletins) == 1,
            "bulletin_text_equal": len(same_probe.bulletins) == 1,
            "middleware_second_freeze_was_noop": (
                len(same_probe.bulletins) == 1 and len(same_run.model_calls) >= 2
            ),
            "calls": same_run.model_calls,
        },
        "new_run_same_bulletin": {
            "bulletin_hash_equal": same_first.bulletin_hash
            == same_second.bulletin_hash,
            "bulletin_text_equal": same_first.text == same_second.text,
            "calls": [*same_a.model_calls, *same_b.model_calls],
        },
        "new_run_changed_bulletin": {
            "bulletin_hash_changed": old.bulletin_hash != changed.bulletin_hash,
            "bulletin_text_changed": old.text != changed.text,
            "stable_prefix_hash_equal": (
                bool(changed_a_prompt.get("stable_prefix_hash"))
                and changed_a_prompt.get("stable_prefix_hash")
                == changed_b_prompt.get("stable_prefix_hash")
            ),
            "stable_prefix_hash": changed_a_prompt.get("stable_prefix_hash"),
            "bulletin_after_stable_prefix": bool(
                changed_a_prompt.get("bulletin_after_stable_prefix")
                and changed_b_prompt.get("bulletin_after_stable_prefix")
            ),
            "calls": [*changed_a.model_calls, *changed_b.model_calls],
        },
        "deep_query_tool_result": {
            "frozen_bulletin_unchanged": (
                len(deep_probe.bulletins) == 1 and len(deep_run.model_calls) >= 2
            ),
            "calls": deep_run.model_calls,
        },
    }
    metrics, call_count, failure_count = _cache_metrics(scenarios)
    repeat_cache_hits = all(
        scenarios[name]["calls"]
        and (scenarios[name]["calls"][-1].get("cache_read_tokens") or 0) > 0
        for name in ("same_run", "new_run_same_bulletin", "deep_query_tool_result")
    )
    invariants = bool(
        scenarios["same_run"]["bulletin_hash_equal"]
        and scenarios["same_run"]["middleware_second_freeze_was_noop"]
        and scenarios["new_run_same_bulletin"]["bulletin_hash_equal"]
        and scenarios["new_run_changed_bulletin"]["bulletin_hash_changed"]
        and scenarios["new_run_changed_bulletin"]["stable_prefix_hash_equal"]
        and scenarios["new_run_changed_bulletin"]["bulletin_after_stable_prefix"]
        and scenarios["deep_query_tool_result"]["frozen_bulletin_unchanged"]
    )
    return {
        "report_version": "memory-cache-integration-v1",
        "mode": "production_integration",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_fingerprint": evaluation_fingerprint(),
        "effective_config": effective_config_snapshot(),
        "fixture_revision": fixture["revision"],
        "runtime_model": ModelConfig.model_name,
        "runtime_max_output_tokens": EVAL_CONFIG["paired_max_output_tokens"],
        "scenarios": scenarios,
        "metrics": metrics,
        "call_count": call_count,
        "failure_count": failure_count,
        "gate_passed": bool(
            invariants
            and repeat_cache_hits
            and call_count >= 8
            and failure_count == 0
            and metrics["cache_read_availability"] == 1.0
            and metrics["ttft_availability"] == 1.0
        ),
    }


async def evaluate(report_dir: Path) -> tuple[dict, dict]:
    fixture = json.loads(
        (ROOT / "fixtures" / "runtime_integration.json").read_text(encoding="utf-8")
    )
    if not await init_knowledge_base() or knowledge_base.client is None:
        raise RuntimeError("Qdrant runtime integration is unavailable")
    client = knowledge_base.client
    if client.collection_exists(TEMP_COLLECTION):
        client.delete_collection(TEMP_COLLECTION)
    config = replace(MachineMemoryConfig, collection_name=TEMP_COLLECTION)
    model = get_llm(temperature_override=0.0).bind(
        max_tokens=EVAL_CONFIG["paired_max_output_tokens"]
    )
    with tempfile.TemporaryDirectory(prefix="noesis-memory-runtime-") as temp:
        workspace_root = Path(temp) / "memory-workspaces"
        with (
            patch("noesis.services.memory.index.MachineMemoryConfig", config),
            patch("noesis.services.memory.bulletin.MachineMemoryConfig", config),
            patch("noesis.services.memory.query.MachineMemoryConfig", config),
            patch.object(memory_paths, "MEMORY_WORKSPACES_ROOT", workspace_root),
        ):
            try:
                async with pg_manager.get_async_session_context() as db:
                    await _reset_users(db)
                    on_items, off_items = await _seed(db, fixture)
                    await MemoryWorkspaceService.rebuild(
                        db, user_id=ON_USER, scope_key=SCOPE
                    )
                    await MemoryWorkspaceService.rebuild(
                        db, user_id=OFF_USER, scope_key=SCOPE
                    )
                    index = MemoryIndexService(client=client)
                    for item in [*on_items, *off_items]:
                        await index.sync_item(
                            db,
                            user_id=str(item.user_id),
                            scope_key=SCOPE,
                            memory_id=item.id,
                        )
                    paired = await _paired_report(db, fixture, index, model)
                    cache = await _cache_report(db, fixture, index, model)
            finally:
                if client.collection_exists(TEMP_COLLECTION):
                    client.delete_collection(TEMP_COLLECTION)
                await close_knowledge_base()
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "paired-integration.json").write_text(
        json.dumps(paired, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (report_dir / "cache-integration.json").write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return paired, cache


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "reports")
    args = parser.parse_args()
    if args.live:
        paired, cache = asyncio.run(evaluate(args.report_dir))
    else:
        raise RuntimeError("runtime integration requires --live")
    print(
        json.dumps(
            {
                "paired": paired["gate_passed"],
                "cache": cache["gate_passed"],
            },
            indent=2,
        )
    )
    return 0 if paired["gate_passed"] and cache["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
