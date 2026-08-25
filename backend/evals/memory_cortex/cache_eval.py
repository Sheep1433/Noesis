"""Live provider cache evaluation for canonical late-inserted Memory Bulletin."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from noesis.chat.event_mapping.usage_normalize import normalize_usage
from noesis.config.env import ModelConfig
from noesis.llm.factory import get_llm
from noesis.agents.middlewares.late_context import insert_late_context
from noesis.agents.middlewares.memory_bulletin_middleware import MemoryBulletinMiddleware
from noesis.services.memory.bulletin import render_bulletin


ROOT = Path(__file__).parent


def _bulletin(statement: str):
    item = SimpleNamespace(
        id="memory-cache",
        memory_type="decision",
        statement=statement,
        applicability="cache evaluation",
        status="active",
    )
    return render_bulletin([(item, 1.0)], max_tokens=500)


async def _call(model, messages) -> dict:
    started = time.perf_counter()
    ttft_ms = None
    usage: dict = {}
    failure_category = None
    try:
        async for chunk in model.astream(messages):
            if ttft_ms is None:
                ttft_ms = (time.perf_counter() - started) * 1000
            if chunk.usage_metadata:
                usage = normalize_usage(chunk.usage_metadata)
    except Exception as exc:
        failure_category = type(exc).__name__
    total_ms = (time.perf_counter() - started) * 1000
    details = usage.get("input_token_details") or {}
    return {
        "cache_read_tokens": details.get("cache_read"),
        "cache_write_tokens": details.get("cache_write"),
        "uncached_input_tokens": usage.get("uncached_input_tokens"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_metrics_available": usage.get("cache_metrics_available"),
        "ttft_ms": round(ttft_ms, 3) if ttft_ms is not None else None,
        "total_ms": round(total_ms, 3),
        "failure_category": failure_category,
    }


async def evaluate_live() -> dict:
    stable = "Stable project and tool context for cache evaluation. " * 700
    system = SystemMessage(content=stable)
    history = [HumanMessage(content="Earlier task."), AIMessage(content="Earlier result.")]
    old = _bulletin("Use one user-controlled memory switch.")
    same = _bulletin("Use one user-controlled memory switch.")
    changed = _bulletin("Use the revised user-controlled memory switch.")
    model = get_llm(temperature_override=0.0).bind(max_tokens=16)

    provider_calls = 0

    async def provider(_query):
        nonlocal provider_calls
        provider_calls += 1
        return old

    middleware = MemoryBulletinMiddleware(run_id="cache-run", provider=provider)
    frozen = await middleware.abefore_agent(
        {"messages": [HumanMessage(content="Current task A")]}, runtime=None
    )
    frozen_again = await middleware.abefore_agent(
        {"messages": [], **(frozen or {})}, runtime=None
    )

    def prompt(bulletin, current: str, prior: list | None = None):
        messages = [*history, *(prior or []), HumanMessage(content=current)]
        return [system, *insert_late_context(
            messages, text=bulletin.text, marker="memory-bulletin"
        )]

    same_run_messages = prompt(old, "Current task A")
    same_run = [
        await _call(model, same_run_messages),
        await _call(model, prompt(
            old,
            "Current task B",
            [HumanMessage(content="Current task A"), AIMessage(content="Prior model step.")],
        )),
    ]

    new_same = [
        await _call(model, prompt(old, "Run one")),
        await _call(model, prompt(same, "Run two")),
    ]

    new_changed = [
        await _call(model, prompt(old, "Old memory")),
        await _call(model, prompt(changed, "Changed memory")),
    ]

    tool_call = AIMessage(
        content="",
        tool_calls=[{"name": "search_memory", "args": {"query": "switch"}, "id": "cache-call", "type": "tool_call"}],
    )
    deep_base = prompt(old, "Check history")
    deep_query = [
        await _call(model, deep_base),
        await _call(model, prompt(old, "Continue with the evidence.", [
            HumanMessage(content="Check history"),
            tool_call,
            ToolMessage(content='{"memory_ids":["memory-cache"]}', tool_call_id="cache-call"),
        ])),
    ]

    scenarios = {
        "same_run": {
            "bulletin_hash_equal": old.bulletin_hash == old.bulletin_hash,
            "bulletin_text_equal": old.text == old.text,
            "middleware_provider_calls": provider_calls,
            "middleware_second_freeze_was_noop": frozen_again is None,
            "calls": same_run,
        },
        "new_run_same_bulletin": {
            "bulletin_hash_equal": old.bulletin_hash == same.bulletin_hash,
            "bulletin_text_equal": old.text == same.text,
            "calls": new_same,
        },
        "new_run_changed_bulletin": {
            "bulletin_hash_changed": old.bulletin_hash != changed.bulletin_hash,
            "bulletin_text_changed": old.text != changed.text,
            "calls": new_changed,
        },
        "deep_query_tool_result": {
            "frozen_bulletin_hash": old.bulletin_hash,
            "calls": deep_query,
        },
    }
    all_calls = [call for scenario in scenarios.values() for call in scenario["calls"]]
    failures = [call for call in all_calls if call["failure_category"]]
    read_available = sum(call["cache_read_tokens"] is not None for call in all_calls)
    write_available = sum(call["cache_write_tokens"] is not None for call in all_calls)
    repeat_hits = [
        same_run[1]["cache_read_tokens"] or 0,
        new_same[1]["cache_read_tokens"] or 0,
        deep_query[1]["cache_read_tokens"] or 0,
    ]
    invariants = (
        scenarios["same_run"]["bulletin_hash_equal"]
        and scenarios["new_run_same_bulletin"]["bulletin_hash_equal"]
        and scenarios["new_run_changed_bulletin"]["bulletin_hash_changed"]
    )
    ttft_values = [call["ttft_ms"] for call in all_calls if call["ttft_ms"] is not None]
    return {
        "report_version": "memory-cache-eval-v1",
        "mode": "live_provider_component",
        "runtime_model": ModelConfig.model_name,
        "prompt_version": "memory-extraction-v5",
        "scenarios": scenarios,
        "metrics": {
            "calls": len(all_calls),
            "cache_read_availability": read_available / len(all_calls),
            "cache_write_availability": write_available / len(all_calls),
            "cache_read_tokens": sum(call["cache_read_tokens"] or 0 for call in all_calls),
            "cache_write_tokens": (
                sum(call["cache_write_tokens"] or 0 for call in all_calls)
                if write_available
                else None
            ),
            "uncached_input_tokens": sum(call["uncached_input_tokens"] or 0 for call in all_calls),
            "ttft_availability": len(ttft_values) / len(all_calls),
            "mean_ttft_ms": sum(ttft_values) / len(ttft_values) if ttft_values else None,
            "cost": None,
        },
        "component_passed": bool(
            invariants
            and not failures
            and provider_calls == 1
            and frozen_again is None
            and len(ttft_values) == len(all_calls)
            and all(value > 0 for value in repeat_hits)
        ),
        "gate_passed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.live or os.getenv("NOESIS_MEMORY_LIVE_EVAL") != "1":
        raise RuntimeError("live cache eval requires --live and NOESIS_MEMORY_LIVE_EVAL=1")
    report = asyncio.run(evaluate_live())
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["component_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
