"""rag：RAG Hit@3（按 scene_name × channel 对账 retrieval_trace）。"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from agent.case_generate.rag import (
    CHANNEL_CURRENT_REQUIREMENT,
    CHANNEL_HISTORICAL_REQUIREMENT,
    CHANNEL_HISTORICAL_TEST_CASES,
)

ALL_CHANNELS = (
    CHANNEL_CURRENT_REQUIREMENT,
    CHANNEL_HISTORICAL_REQUIREMENT,
    CHANNEL_HISTORICAL_TEST_CASES,
)


def score_rag(
    run_output: Dict[str, Any],
    ground_truth: Dict[str, Any],
) -> Dict[str, Any]:
    expected_rag: Dict[str, Any] = dict(ground_truth.get("expected_rag") or {})
    if not expected_rag:
        return {
            "skipped": True,
            "rag_hit_at_3": None,
            "rag_hit_at_3_by_channel": {},
            "rag_eval_incomplete": False,
            "channel_checks": 0,
            "channel_hits": 0,
        }

    state = run_output.get("state") or {}
    trace: Dict[str, Any] = dict(state.get("retrieval_trace") or {})
    if not trace:
        return {
            "skipped": False,
            "rag_hit_at_3": None,
            "rag_hit_at_3_by_channel": {},
            "rag_eval_incomplete": True,
            "channel_checks": 0,
            "channel_hits": 0,
            "error": "retrieval_trace 为空",
        }

    checks: List[Tuple[str, str, bool]] = []
    for scene_name, scene_exp in expected_rag.items():
        if not isinstance(scene_exp, dict):
            continue
        trace_entry = trace.get(scene_name) or {}
        channels_trace = trace_entry.get("channels") or {}

        for channel in ALL_CHANNELS:
            ch_exp = scene_exp.get(channel)
            if not isinstance(ch_exp, dict):
                continue
            expected_ids = {str(x) for x in (ch_exp.get("expected_ids") or []) if str(x).strip()}
            if not expected_ids:
                continue

            hit_ids = set(
                str(x)
                for x in ((channels_trace.get(channel) or {}).get("hit_ids") or [])
                if str(x).strip()
            )
            hit = bool(expected_ids & hit_ids)
            checks.append((scene_name, channel, hit))

    if not checks:
        return {
            "skipped": True,
            "rag_hit_at_3": None,
            "rag_hit_at_3_by_channel": {},
            "rag_eval_incomplete": False,
            "channel_checks": 0,
            "channel_hits": 0,
        }

    hits_n = sum(1 for _, _, h in checks if h)
    rag_hit_at_3 = hits_n / len(checks)

    by_channel: Dict[str, List[bool]] = {c: [] for c in ALL_CHANNELS}
    for _, channel, hit in checks:
        by_channel[channel].append(hit)
    rag_hit_at_3_by_channel = {
        ch: round(sum(v) / len(v), 4)
        for ch, v in by_channel.items()
        if v
    }

    return {
        "skipped": False,
        "rag_hit_at_3": round(rag_hit_at_3, 4),
        "rag_hit_at_3_by_channel": rag_hit_at_3_by_channel,
        "rag_eval_incomplete": False,
        "channel_checks": len(checks),
        "channel_hits": hits_n,
        "details": [
            {"scene_name": s, "channel": c, "hit": h}
            for s, c, h in checks
        ],
    }
