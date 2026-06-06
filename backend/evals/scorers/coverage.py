"""coverage：测试点覆盖准确率（LLM Judge）。"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

from llm import get_llm

CoverageJudgeFn = Callable[[List[Dict[str, Any]], List[Dict[str, Any]]], List[Dict[str, Any]]]


def score_coverage(
    run_output: Dict[str, Any],
    ground_truth: Dict[str, Any],
    *,
    judge_fn: Optional[CoverageJudgeFn] = None,
) -> Dict[str, Any]:
    golden: List[Dict[str, Any]] = list(ground_truth.get("golden_test_points") or [])
    if not golden:
        return {
            "skipped": True,
            "point_coverage_recall": None,
            "point_precision": None,
            "covered_count": None,
            "golden_count": 0,
            "judgments": [],
        }

    state = run_output.get("state") or {}
    scenes = state.get("scenes_testpoints") or []
    if not scenes:
        return {
            "skipped": False,
            "point_coverage_recall": 0.0,
            "point_precision": 0.0,
            "covered_count": 0,
            "golden_count": len(golden),
            "judgments": [],
            "error": "scenes_testpoints 为空",
        }

    judge = judge_fn or _llm_judge_coverage
    try:
        judgments = judge(golden, scenes)
    except Exception as e:
        return {
            "skipped": False,
            "point_coverage_recall": 0.0,
            "point_precision": None,
            "covered_count": 0,
            "golden_count": len(golden),
            "judgments": [],
            "error": str(e),
        }

    covered_count = sum(1 for j in judgments if j.get("covered"))
    recall = covered_count / len(golden)

    gen_names = _collect_generated_point_names(scenes)
    matched_gen = {
        n
        for j in judgments
        for n in (j.get("matched_point_names") or [])
        if n
    }
    precision = (len(matched_gen) / len(gen_names)) if gen_names else None

    return {
        "skipped": False,
        "point_coverage_recall": round(recall, 4),
        "point_precision": round(precision, 4) if precision is not None else None,
        "covered_count": covered_count,
        "golden_count": len(golden),
        "judgments": judgments,
    }


def _collect_generated_point_names(scenes: List[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    for scene in scenes:
        for tp in scene.get("test_points") or []:
            n = str(tp.get("point_name") or "").strip()
            if n:
                names.append(n)
    return names


def _llm_judge_coverage(
    golden: List[Dict[str, Any]],
    scenes: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    prompt = _build_coverage_prompt(golden, scenes)
    llm = get_llm()
    response = llm.invoke(prompt)
    content = response.content if hasattr(response, "content") else str(response)
    return _parse_coverage_judgments(content, golden)


def _build_coverage_prompt(
    golden: List[Dict[str, Any]],
    scenes: List[Dict[str, Any]],
) -> str:
    return f"""你是测试设计评审员。判断「生成测试点」是否**语义覆盖**每条「金标准测试点」。
允许多个生成点共同覆盖一条金标准；生成点 scene_name 可与金标准不完全一致，以语义为准。

## 金标准测试点
{json.dumps(golden, ensure_ascii=False, indent=2)}

## 生成场景与测试点
{json.dumps(scenes, ensure_ascii=False, indent=2)}

仅输出 JSON（不要其它文字）：
```json
{{
  "judgments": [
    {{
      "scene_name": "<金标准 scene_name>",
      "point_name": "<金标准 point_name>",
      "covered": true,
      "matched_point_names": ["<生成 point_name>", "..."]
    }}
  ]
}}
```
judgments 数组长度 MUST 等于金标准条数，顺序与金标准一致。"""


def _parse_coverage_judgments(
    content: str,
    golden: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    text = content.strip()
    block = re.search(r"```json\s*([\s\S]*?)\s*```", text)
    if block:
        text = block.group(1)
    else:
        start = text.find("{")
        if start >= 0:
            text = text[start:]
    data = json.loads(text)
    raw = data.get("judgments")
    if not isinstance(raw, list):
        raise ValueError("Judge 输出缺少 judgments 数组")

    out: List[Dict[str, Any]] = []
    for i, g in enumerate(golden):
        row = raw[i] if i < len(raw) and isinstance(raw[i], dict) else {}
        out.append(
            {
                "scene_name": g.get("scene_name"),
                "point_name": g.get("point_name"),
                "covered": bool(row.get("covered")),
                "matched_point_names": list(row.get("matched_point_names") or []),
            }
        )
    return out
