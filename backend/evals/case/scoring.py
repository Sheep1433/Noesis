"""评测评分：L0 / rag 规则断言；coverage 由 promptfoo llm-rubric 负责。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# promptfoo 子进程加载本文件时无 backend PYTHONPATH
_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agent.case_generate.rag import (
    CHANNEL_CURRENT_REQUIREMENT,
    CHANNEL_HISTORICAL_REQUIREMENT,
    CHANNEL_HISTORICAL_TEST_CASES,
)

GradingResult = Dict[str, Union[bool, float, str, None, Dict[str, Any]]]

ALL_RAG_CHANNELS = (
    CHANNEL_CURRENT_REQUIREMENT,
    CHANNEL_HISTORICAL_REQUIREMENT,
    CHANNEL_HISTORICAL_TEST_CASES,
)


# --- L0 ---


def _scene_ok(scene: Dict[str, Any]) -> bool:
    if not scene.get("scene_name"):
        return False
    tps = scene.get("test_points")
    if not isinstance(tps, list):
        return False
    for tp in tps:
        if not isinstance(tp, dict):
            return False
        if not tp.get("point_name"):
            return False
        for key in ("point_level", "point_type"):
            if key not in tp:
                return False
    return True


def _case_ok(case: Dict[str, Any]) -> bool:
    for key in ("case_id", "point_name", "test_steps", "expected_results"):
        if key not in case:
            return False
    if not isinstance(case.get("test_steps"), list):
        return False
    if not isinstance(case.get("expected_results"), list):
        return False
    return True


def score_l0(run_output: Dict[str, Any]) -> Dict[str, Any]:
    state = run_output.get("state") or {}
    scenes: List[Dict[str, Any]] = state.get("scenes_testpoints") or []
    cases: List[Dict[str, Any]] = state.get("test_cases") or []
    error = state.get("error")

    phase_no_error = error is None
    json_parse_ok = isinstance(scenes, list) and len(scenes) > 0
    schema_ok = json_parse_ok and all(_scene_ok(s) for s in scenes)
    if cases:
        schema_ok = schema_ok and all(_case_ok(c) for c in cases)
    passed = phase_no_error and schema_ok

    failure_reason = None
    if not passed:
        if not phase_no_error:
            failure_reason = str(error or "phase error")
        elif not json_parse_ok:
            failure_reason = "scenes_testpoints 为空或非法"
        elif not schema_ok:
            failure_reason = "schema 校验失败"
        else:
            failure_reason = "unknown"

    return {
        "phase_no_error": phase_no_error,
        "json_parse_ok": json_parse_ok,
        "schema_ok": schema_ok,
        "passed": passed,
        "failure_reason": failure_reason,
    }


# --- rag ---


def score_rag(run_output: Dict[str, Any], ground_truth: Dict[str, Any]) -> Dict[str, Any]:
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
        for channel in ALL_RAG_CHANNELS:
            ch_exp = scene_exp.get(channel)
            if not isinstance(ch_exp, dict):
                continue
            expected_ids = {str(x) for x in (ch_exp.get("expected_ids") or []) if str(x).strip()}
            if not expected_ids:
                continue
            hit_ids = {
                str(x)
                for x in ((channels_trace.get(channel) or {}).get("hit_ids") or [])
                if str(x).strip()
            }
            checks.append((scene_name, channel, bool(expected_ids & hit_ids)))

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
    by_channel: Dict[str, List[bool]] = {c: [] for c in ALL_RAG_CHANNELS}
    for _, channel, hit in checks:
        by_channel[channel].append(hit)

    return {
        "skipped": False,
        "rag_hit_at_3": round(hits_n / len(checks), 4),
        "rag_hit_at_3_by_channel": {
            ch: round(sum(v) / len(v), 4) for ch, v in by_channel.items() if v
        },
        "rag_eval_incomplete": False,
        "channel_checks": len(checks),
        "channel_hits": hits_n,
        "details": [{"scene_name": s, "channel": c, "hit": h} for s, c, h in checks],
    }


# --- promptfoo 断言 ---


def _parse_provider_output(output: str) -> Dict[str, Any]:
    text = (output or "").strip()
    return json.loads(text) if text else {}


def _ground_truth(context: Dict[str, Any]) -> Dict[str, Any]:
    vars_ = context.get("vars") or {}
    gt = vars_.get("ground_truth")
    if isinstance(gt, dict):
        return dict(gt)
    item = vars_.get("item")
    if isinstance(item, dict):
        item_gt = item.get("ground_truth")
        return dict(item_gt) if isinstance(item_gt, dict) else {}
    raise ValueError("测试用例缺少 vars.ground_truth 或 vars.item")


def _grading(
    *,
    metric: str,
    score: Optional[float],
    passed: bool,
    reason: str,
) -> GradingResult:
    return {
        "pass": passed,
        "score": score if score is not None else 0.0,
        "reason": reason,
        "metadata": {"metric": metric},
    }


def _skipped(reason: str) -> GradingResult:
    return {"pass": True, "score": 1.0, "reason": reason}


def assert_l0(output: str, context: Dict[str, Any]) -> Union[bool, float, GradingResult]:
    result = score_l0(_parse_provider_output(output))
    passed = bool(result.get("passed"))
    return _grading(
        metric="l0",
        score=1.0 if passed else 0.0,
        passed=passed,
        reason=result.get("failure_reason") or ("L0 通过" if passed else "L0 未通过"),
    )


def assert_rag(output: str, context: Dict[str, Any]) -> Union[bool, float, GradingResult]:
    result = score_rag(_parse_provider_output(output), _ground_truth(context))
    if result.get("skipped"):
        return _skipped("无 expected_rag")
    if result.get("rag_eval_incomplete"):
        return _grading(
            metric="rag_hit_at_3",
            score=0.0,
            passed=False,
            reason=result.get("error") or "rag_eval_incomplete",
        )

    score = result.get("rag_hit_at_3")
    return _grading(
        metric="rag_hit_at_3",
        score=float(score) if score is not None else 0.0,
        passed=True,
        reason=f"rag_hit_at_3={score}",
    )
