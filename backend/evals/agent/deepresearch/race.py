"""RACE 判分（DeepResearch Bench 官方方法论的移植）。

官方口径（github.com/Ayanami0730/deep_research_bench，Apache-2.0）：
- 裁判对照该题的「自适应评判标准列表」给我们与专家参考报告同时打分
  （每条标准 0-10，两篇各一份），判分 prompt 为官方中文原版
  （race_prompt_zh.txt，原样提取自 prompt/score_prompt_zh.py）
- 每条标准按其权重加权求均值 → 维度分；维度按该题 dimension_weight
  加权求和 → 总分
- headline = 我方总分 / (我方总分 + 参考总分)：50 分 = 与专家参考持平，
  榜单头部约 58（相对分口径，非绝对质量百分制）
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

RACE_ROOT = Path(__file__).resolve().parent
RACE_DATA_DIR = RACE_ROOT / "fixtures" / "race_data"
RACE_PROMPT_PATH = RACE_ROOT / "race_prompt_zh.txt"

DIMS = ("comprehensiveness", "insight", "instruction_following", "readability")


def load_race_data() -> tuple[dict[int, dict], dict[int, dict]]:
    """返回 ({id: 参考报告}, {id: 评分标准})；缺数据时报错并给出补齐方式。"""
    ref_path = RACE_DATA_DIR / "reference.jsonl"
    crit_path = RACE_DATA_DIR / "criteria.jsonl"
    if not ref_path.is_file() or not crit_path.is_file():
        raise FileNotFoundError(
            f"缺 RACE 判分数据（{RACE_DATA_DIR}）：从官方仓库 "
            "github.com/Ayanami0730/deep_research_bench 的 "
            "data/test_data/raw_data/reference.jsonl 与 "
            "data/criteria_data/criteria.jsonl 中提取所用题目的子集放入该目录"
        )
    refs = {r["id"]: r for r in map(json.loads, ref_path.read_text(encoding="utf-8").splitlines()) if r}
    crits = {c["id"]: c for c in map(json.loads, crit_path.read_text(encoding="utf-8").splitlines()) if c}
    return refs, crits


def format_criteria_list(criteria: dict[str, Any]) -> str:
    """标准列表序列化为 prompt 片段（与官方一致：不带权重，防裁判偏置）。"""
    out: dict[str, list[dict]] = {}
    for dim, items in (criteria.get("criterions") or {}).items():
        if not isinstance(items, list):
            continue
        out[dim] = [
            {"criterion": it["criterion"], "explanation": it["explanation"]}
            for it in items
            if isinstance(it, dict) and "criterion" in it and "explanation" in it
        ]
    return json.dumps(out, ensure_ascii=False)


def build_race_prompt(
    task_prompt: str, article_ours: str, article_ref: str, criteria: dict[str, Any]
) -> str:
    template = RACE_PROMPT_PATH.read_text(encoding="utf-8")
    return template.format(
        task_prompt=task_prompt,
        article_1=article_ours,
        article_2=article_ref,
        criteria_list=format_criteria_list(criteria),
    )


def parse_judge_output(raw: str) -> dict[str, list[dict]]:
    """解析裁判输出：容忍 markdown 代码栏；四维齐备才算有效。"""
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence:
        text = fence.group(1).strip()
    brace = re.search(r"\{[\s\S]*\}", text)
    if not brace:
        raise ValueError(f"裁判输出无 JSON: {raw[:200]!r}")
    parsed = json.loads(brace.group(0))
    missing = [d for d in DIMS if d not in parsed]
    if missing:
        raise ValueError(f"裁判输出缺维度: {missing}")
    return parsed


def _lookup_weight(dim_map: dict[str, float], criterion: str) -> float | None:
    """标准文本 → 权重：精确 → 忽略大小写 → 子串包含（官方同序）。"""
    weight = dim_map.get(criterion)
    if weight is not None:
        return weight
    lowered = criterion.lower()
    for key, val in dim_map.items():
        if key.lower() == lowered:
            return val
    for key, val in dim_map.items():
        if lowered in key.lower() or key.lower() in lowered:
            return val
    return None


def calculate_weighted_scores(
    judge_output: dict[str, list[dict]], criteria: dict[str, Any]
) -> dict[str, Any]:
    """官方算法：条目按权重加权均值 → 维度分；维度按 dimension_weight 加权 → 总分。"""
    dim_weights = criteria.get("dimension_weight") or {}
    criterion_weights = {
        dim: {c["criterion"]: c["weight"] for c in items}
        for dim, items in (criteria.get("criterions") or {}).items()
    }
    result = {"target": {"dims": {}, "total": 0.0},
              "reference": {"dims": {}, "total": 0.0}}
    for dim, score_items in judge_output.items():
        if not isinstance(score_items, list) or dim not in dim_weights:
            continue
        dim_map = criterion_weights.get(dim) or {}
        if not dim_map:
            continue
        t_weighted = r_weighted = total_w = 0.0
        for item in score_items:
            if not isinstance(item, dict):
                continue
            criterion = str(item.get("criterion") or "").strip()
            try:
                a1 = float(item.get("article_1_score"))
                a2_raw = item.get("article_2_score")
                a2 = float(a2_raw) if a2_raw is not None else None
            except (TypeError, ValueError):
                continue
            if not criterion or a1 is None:
                continue
            weight = _lookup_weight(dim_map, criterion)
            if weight is None:
                # 官方口径：匹配不上的条目用该维度平均权重兜底
                weight = sum(dim_map.values()) / len(dim_map)
            t_weighted += a1 * weight
            total_w += weight
            if a2 is not None:
                r_weighted += a2 * weight
        if total_w > 0:
            result["target"]["dims"][dim] = t_weighted / total_w
            result["reference"]["dims"][dim] = r_weighted / total_w
            result["target"]["total"] += (t_weighted / total_w) * dim_weights[dim]
            result["reference"]["total"] += (r_weighted / total_w) * dim_weights[dim]
    return result


def race_record(
    *,
    task_prompt: str,
    article_ours: str,
    reference: dict[str, Any],
    criteria: dict[str, Any],
    judge_output: dict[str, list[dict]],
) -> dict[str, Any]:
    """官方口径的相对分：overall = 我方 / (我方 + 参考)，50 = 与专家持平。"""
    scores = calculate_weighted_scores(judge_output, criteria)
    t_total, r_total = scores["target"]["total"], scores["reference"]["total"]
    overall = t_total / (t_total + r_total) if (t_total + r_total) > 0 else 0.0
    dims = {}
    for dim in DIMS:
        t = scores["target"]["dims"].get(dim, 0.0)
        r = scores["reference"]["dims"].get(dim, 0.0)
        dims[dim] = t / (t + r) if (t + r) > 0 else 0.0
    return {
        "overall_score": round(overall, 4),
        **{d: round(v, 4) for d, v in dims.items()},
        "target_total": round(t_total, 4),
        "reference_total": round(r_total, 4),
        "task_prompt": task_prompt,
    }
