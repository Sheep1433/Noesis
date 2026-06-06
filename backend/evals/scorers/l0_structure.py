"""L0：结构门禁（schema / 无 error）。"""

from __future__ import annotations

from typing import Any, Dict, List


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
    required = (
        "case_id",
        "point_name",
        "test_steps",
        "expected_results",
    )
    for key in required:
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

    return {
        "phase_no_error": phase_no_error,
        "json_parse_ok": json_parse_ok,
        "schema_ok": schema_ok,
        "passed": passed,
        "failure_reason": None if passed else _failure_reason(phase_no_error, json_parse_ok, schema_ok, error),
    }


def _failure_reason(
    phase_no_error: bool,
    json_parse_ok: bool,
    schema_ok: bool,
    error: Any,
) -> str:
    if not phase_no_error:
        return str(error or "phase error")
    if not json_parse_ok:
        return "scenes_testpoints 为空或非法"
    if not schema_ok:
        return "schema 校验失败"
    return "unknown"
