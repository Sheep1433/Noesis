"""promptfoo python 断言：阶段 A L0/coverage 辅助 + 阶段 B RAG Recall@K。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

_BACKEND = Path(__file__).resolve().parents[3]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agent.case_generate.rag import (
    CHANNEL_HISTORICAL_REQUIREMENT,
    CHANNEL_HISTORICAL_TEST_CASES,
)

GradingResult = Dict[str, Union[bool, float, str, None, Dict[str, Any]]]

STAGE_B_CHANNELS = (
    CHANNEL_HISTORICAL_REQUIREMENT,
    CHANNEL_HISTORICAL_TEST_CASES,
)


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


def _golden_scene_names(ground_truth: Dict[str, Any]) -> List[str]:
    explicit = ground_truth.get("golden_scene_names")
    if isinstance(explicit, list) and explicit:
        return sorted({str(x).strip() for x in explicit if str(x).strip()})
    names: set[str] = set()
    for tp in ground_truth.get("golden_test_points") or []:
        if isinstance(tp, dict):
            n = str(tp.get("scene_name") or "").strip()
            if n:
                names.add(n)
    return sorted(names)


def score_scene_name_recall(run_output: Dict[str, Any], ground_truth: Dict[str, Any]) -> Dict[str, Any]:
    golden = _golden_scene_names(ground_truth)
    if not golden:
        return {"skipped": True, "scene_name_recall": None}

    generated: set[str] = set()
    for scene in (run_output.get("state") or {}).get("scenes_testpoints") or []:
        if isinstance(scene, dict):
            n = str(scene.get("scene_name") or "").strip()
            if n:
                generated.add(n)

    hits = sum(1 for n in golden if n in generated)
    return {
        "skipped": False,
        "scene_name_recall": round(hits / len(golden), 4),
        "golden_scene_names": golden,
        "generated_scene_names": sorted(generated),
    }


def _channel_metrics(
    hit_ids: List[str],
    relevant_ids: List[str],
    *,
    k: int,
) -> Dict[str, float]:
    topk = [str(x) for x in hit_ids[:k] if str(x).strip()]
    relevant = {str(x) for x in relevant_ids if str(x).strip()}
    if not relevant:
        return {"recall_at_k": 0.0, "hit_at_k": 0.0, "mrr_at_k": 0.0}

    inter = relevant & set(topk)
    recall = len(inter) / len(relevant)
    hit = 1.0 if inter else 0.0
    mrr = 0.0
    for rank, hid in enumerate(topk, start=1):
        if hid in relevant:
            mrr = 1.0 / rank
            break
    return {
        "recall_at_k": round(recall, 4),
        "hit_at_k": hit,
        "mrr_at_k": round(mrr, 4),
    }


def score_stage_b_channel(
    run_output: Dict[str, Any],
    stage_b_scene: Dict[str, Any],
    channel: str,
) -> Dict[str, Any]:
    expected_rag = stage_b_scene.get("expected_rag") or {}
    ch_exp = expected_rag.get(channel) if isinstance(expected_rag, dict) else None
    if not isinstance(ch_exp, dict):
        return {"skipped": True, "reason": f"无 {channel} 标注"}

    relevant_ids = [str(x) for x in (ch_exp.get("relevant_ids") or []) if str(x).strip()]
    if not relevant_ids:
        return {"skipped": True, "reason": f"{channel} relevant_ids 为空"}

    k = int(ch_exp.get("k") or 3)
    scene_name = str(stage_b_scene.get("scene_name") or "").strip()
    trace_root = run_output.get("retrieval_trace") or {}
    trace_entry = trace_root.get(scene_name) or {}
    channels_trace = trace_entry.get("channels") or {}
    hit_ids = (channels_trace.get(channel) or {}).get("hit_ids") or []

    if not trace_entry:
        return {
            "skipped": False,
            "incomplete": True,
            "error": f"retrieval_trace 缺少 scene={scene_name}",
        }

    metrics = _channel_metrics(hit_ids, relevant_ids, k=k)
    return {
        "skipped": False,
        "incomplete": False,
        "scene_name": scene_name,
        "channel": channel,
        **metrics,
    }


def _parse_output(output: str) -> Dict[str, Any]:
    text = (output or "").strip()
    return json.loads(text) if text else {}


def _ground_truth(context: Dict[str, Any]) -> Dict[str, Any]:
    gt = (context.get("vars") or {}).get("ground_truth")
    if isinstance(gt, dict):
        return dict(gt)
    return {}


def _rag_scene(context: Dict[str, Any]) -> Dict[str, Any]:
    vars_ = context.get("vars") or {}
    scene = vars_.get("rag_scene")
    if isinstance(scene, dict):
        return dict(scene)
    raise ValueError("缺少 vars.rag_scene")


def _grading(*, metric: str, score: Optional[float], passed: bool, reason: str) -> GradingResult:
    return {
        "pass": passed,
        "score": score if score is not None else 0.0,
        "reason": reason,
        "metadata": {"metric": metric},
    }


def assert_l0(output: str, context: Dict[str, Any]) -> Union[bool, float, GradingResult]:
    result = score_l0(_parse_output(output))
    passed = bool(result.get("passed"))
    return _grading(
        metric="l0",
        score=1.0 if passed else 0.0,
        passed=passed,
        reason=result.get("failure_reason") or ("L0 通过" if passed else "L0 未通过"),
    )


def assert_scene_name_recall(output: str, context: Dict[str, Any]) -> Union[bool, float, GradingResult]:
    result = score_scene_name_recall(_parse_output(output), _ground_truth(context))
    if result.get("skipped"):
        return {"pass": True, "score": 1.0, "reason": "无 golden_scene_names"}
    score = float(result.get("scene_name_recall") or 0.0)
    return _grading(
        metric="scene_name_recall",
        score=score,
        passed=True,
        reason=f"scene_name_recall={score}",
    )


def _assert_channel_metric(
    output: str,
    context: Dict[str, Any],
    *,
    channel: str,
    metric_suffix: str,
) -> GradingResult:
    run_output = _parse_output(output)
    scene = _rag_scene(context)
    result = score_stage_b_channel(run_output, scene, channel)
    metric = f"{channel}_{metric_suffix}"

    if result.get("skipped"):
        return {"pass": True, "score": 1.0, "reason": result.get("reason") or "skipped"}
    if result.get("incomplete"):
        return _grading(
            metric=metric,
            score=0.0,
            passed=False,
            reason=str(result.get("error") or "incomplete"),
        )
    key = metric_suffix.replace("_at_3", "_at_k") if metric_suffix.endswith("_at_3") else metric_suffix
    if metric_suffix == "recall_at_3":
        key = "recall_at_k"
    elif metric_suffix == "hit_at_3":
        key = "hit_at_k"
    elif metric_suffix == "mrr_at_3":
        key = "mrr_at_k"
    score = float(result.get(key) or 0.0)
    return _grading(metric=metric, score=score, passed=True, reason=f"{metric}={score}")


def assert_historical_requirements_recall_at_3(output: str, context: Dict[str, Any]) -> GradingResult:
    return _assert_channel_metric(
        output, context, channel=CHANNEL_HISTORICAL_REQUIREMENT, metric_suffix="recall_at_3"
    )


def assert_historical_requirements_hit_at_3(output: str, context: Dict[str, Any]) -> GradingResult:
    return _assert_channel_metric(
        output, context, channel=CHANNEL_HISTORICAL_REQUIREMENT, metric_suffix="hit_at_3"
    )


def assert_historical_test_cases_recall_at_3(output: str, context: Dict[str, Any]) -> GradingResult:
    return _assert_channel_metric(
        output, context, channel=CHANNEL_HISTORICAL_TEST_CASES, metric_suffix="recall_at_3"
    )


def assert_historical_test_cases_hit_at_3(output: str, context: Dict[str, Any]) -> GradingResult:
    return _assert_channel_metric(
        output, context, channel=CHANNEL_HISTORICAL_TEST_CASES, metric_suffix="hit_at_3"
    )


def assert_document_context_present(output: str, context: Dict[str, Any]) -> GradingResult:
    run_output = _parse_output(output)
    injected = bool(run_output.get("document_context_injected"))
    return _grading(
        metric="document_context_present",
        score=1.0 if injected else 0.0,
        passed=injected,
        reason="document_context 已注入 prompt" if injected else "document_context 未注入",
    )
