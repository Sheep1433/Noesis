"""深度研究 Agent 混合评分：规则 criteria + semantic_rubric LLM Judge。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from llm import get_llm

RULE_WEIGHT = 0.7
SEMANTIC_WEIGHT = 0.3

SemanticJudgeFn = Callable[[str, List[str]], List[Dict[str, Any]]]


def _resolve_path(workspace_path: Path, rel_path: str) -> Path:
    rel = rel_path.lstrip("/")
    target = (workspace_path / rel).resolve()
    root = workspace_path.resolve()
    if root not in target.parents and target != root:
        raise ValueError(f"非法路径（目录穿越）: {rel_path}")
    return target


def _get_json_field(data: Any, field_path: str) -> Any:
    current = data
    for part in field_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def evaluate_criterion(workspace_path: Path, criterion: Dict[str, Any]) -> Dict[str, Any]:
    cid = str(criterion.get("id") or "")
    ctype = str(criterion.get("type") or "")
    rel_path = str(criterion.get("path") or "")

    try:
        if ctype == "file_exists":
            target = _resolve_path(workspace_path, rel_path)
            passed = target.is_file()
            return {
                "id": cid,
                "type": ctype,
                "passed": passed,
                "detail": f"{'存在' if passed else '缺失'}: {rel_path}",
            }

        if ctype == "file_not_exists":
            target = _resolve_path(workspace_path, rel_path)
            passed = not target.is_file()
            return {
                "id": cid,
                "type": ctype,
                "passed": passed,
                "detail": f"{'未创建' if passed else '不应存在但已创建'}: {rel_path}",
            }

        if ctype == "file_contains":
            target = _resolve_path(workspace_path, rel_path)
            if not target.is_file():
                return {
                    "id": cid,
                    "type": ctype,
                    "passed": False,
                    "detail": f"文件不存在: {rel_path}",
                }
            needle = str(criterion.get("substring") or "")
            content = target.read_text(encoding="utf-8", errors="replace")
            passed = needle in content
            return {
                "id": cid,
                "type": ctype,
                "passed": passed,
                "detail": f"{'包含' if passed else '未包含'}: {needle!r}",
            }

        if ctype == "json_field_min":
            target = _resolve_path(workspace_path, rel_path)
            if not target.is_file():
                return {
                    "id": cid,
                    "type": ctype,
                    "passed": False,
                    "detail": f"JSON 文件不存在: {rel_path}",
                }
            data = json.loads(target.read_text(encoding="utf-8"))
            field_name = str(criterion.get("field") or "")
            value = _get_json_field(data, field_name)
            min_val = criterion.get("min")
            if value is None:
                passed = False
            else:
                try:
                    passed = float(value) >= float(min_val)
                except (TypeError, ValueError):
                    passed = False
            return {
                "id": cid,
                "type": ctype,
                "passed": passed,
                "detail": f"{field_name}={value!r}, min={min_val!r}",
            }

        return {
            "id": cid,
            "type": ctype,
            "passed": False,
            "detail": f"未知 criterion type: {ctype}",
        }
    except Exception as e:
        return {
            "id": cid,
            "type": ctype,
            "passed": False,
            "detail": str(e),
        }


def score_rule_criteria(
    workspace_path: Path,
    criteria: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], float]:
    if not criteria:
        return [], 1.0
    results = [evaluate_criterion(workspace_path, c) for c in criteria]
    passed = sum(1 for r in results if r.get("passed"))
    rate = passed / len(results)
    return results, rate


def _parse_semantic_judgments(content: str, rubric: List[str]) -> List[Dict[str, Any]]:
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
    for i, rubric_item in enumerate(rubric):
        row = raw[i] if i < len(raw) and isinstance(raw[i], dict) else {}
        out.append(
            {
                "rubric": rubric_item,
                "passed": bool(row.get("passed")),
                "reason": str(row.get("reason") or ""),
            }
        )
    return out


def _build_semantic_prompt(final_text: str, rubric: List[str]) -> str:
    return f"""你是深度研究 Agent 评测裁判。根据「Agent 最终输出」判断每条 rubric 是否满足。
仅依据给定文本判断，不要臆测未出现的信息。每条 rubric 独立判定，输出 passed=true/false。

## Agent 最终输出
{final_text or "（空）"}

## Rubric
{json.dumps(rubric, ensure_ascii=False, indent=2)}

仅输出 JSON（不要其它文字）：
```json
{{
  "judgments": [
    {{"passed": true, "reason": "..."}}
  ]
}}
```
judgments 数组长度 MUST 等于 rubric 条数，顺序一致。"""


def _llm_judge_semantic(final_text: str, rubric: List[str]) -> List[Dict[str, Any]]:
    llm = get_llm()
    response = llm.invoke(_build_semantic_prompt(final_text, rubric))
    content = response.content if hasattr(response, "content") else str(response)
    return _parse_semantic_judgments(str(content), rubric)


def score_semantic_rubric(
    final_text: str,
    rubric: List[str],
    *,
    judge_fn: Optional[SemanticJudgeFn] = None,
) -> Dict[str, Any]:
    if not rubric:
        return {"skipped": True, "judgments": [], "semantic_score": None}

    judge = judge_fn or _llm_judge_semantic
    try:
        judgments = judge(final_text, rubric)
    except Exception as e:
        return {
            "skipped": False,
            "judgments": [],
            "semantic_score": 0.0,
            "error": str(e),
        }

    passed = sum(1 for j in judgments if j.get("passed"))
    rate = passed / len(rubric)
    return {
        "skipped": False,
        "judgments": judgments,
        "semantic_score": round(rate, 4),
    }


def score_item(
    run_output: Dict[str, Any],
    ground_truth: Dict[str, Any],
    workspace_path: Path,
    *,
    judge_fn: Optional[SemanticJudgeFn] = None,
    skip_semantic: bool = False,
) -> Dict[str, Any]:
    criteria = list(ground_truth.get("criteria") or [])
    rubric = list(ground_truth.get("semantic_rubric") or [])

    rule_results, rule_score = score_rule_criteria(workspace_path, criteria)

    semantic_result: Dict[str, Any]
    if skip_semantic or not rubric:
        semantic_result = {"skipped": True, "judgments": [], "semantic_score": None}
    else:
        semantic_result = score_semantic_rubric(
            str(run_output.get("final_text") or ""),
            rubric,
            judge_fn=judge_fn,
        )

    semantic_score = semantic_result.get("semantic_score")
    if semantic_score is None:
        overall_score = round(rule_score, 4)
    else:
        overall_score = round(
            RULE_WEIGHT * rule_score + SEMANTIC_WEIGHT * float(semantic_score),
            4,
        )

    expected_tools = list(ground_truth.get("expected_tools") or [])
    tool_stats = dict(run_output.get("tool_stats") or {})
    tool_coverage = None
    if expected_tools:
        matched = [t for t in expected_tools if tool_stats.get(t, 0) > 0]
        tool_coverage = {
            "expected": expected_tools,
            "matched": matched,
            "rate": round(len(matched) / len(expected_tools), 4),
        }

    return {
        "rule_criteria": rule_results,
        "rule_score": round(rule_score, 4),
        "semantic": semantic_result,
        "semantic_score": semantic_score,
        "overall_score": overall_score,
        "tool_coverage": tool_coverage,
        "passed": bool(run_output.get("completed"))
        and not run_output.get("error")
        and all(r.get("passed") for r in rule_results),
    }
