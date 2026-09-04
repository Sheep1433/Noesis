"""Judge rubric：headline recall 判卷（2/1/0）+ 五维 0–5 诊断维度。

recall 决定 headline（recall% @ retained tokens）；五维只进 raw 记录做失败诊断。
judge 可见 reference，作答方不可见。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

DIMENSIONS: List[str] = [
    "accuracy",
    "artifact_trail",
    "context_awareness",
    "continuity",
    "completeness",
]

# headline 判分：2=正确 / 1=部分 / 0=错误
RECALL_VALUES = (2, 1, 0)

DIMENSION_DESCRIPTIONS: Dict[str, str] = {
    "accuracy": (
        "与 reference 事实一致：错误码、配置项、文件路径、根因描述等。"
        "单处关键事实错误应明显扣分。"
    ),
    "artifact_trail": (
        "文件路径、配置项、命令、工具名等可追溯信息是否保留且正确。"
    ),
    "context_awareness": (
        "是否反映会话当前阶段与最终状态，而非中途已废弃的中间结论。"
    ),
    "continuity": (
        "仅凭该答案能否继续未完成任务，无需重新探索全部上下文。"
    ),
    "completeness": (
        "是否覆盖 probe 问题的全部要点；漏答任一要项应扣分。"
    ),
}

SCORE_SCALE: Dict[int, str] = {
    0: "无有效信息或明显错误/臆造。",
    1: "重大缺失或关键事实错误。",
    2: "部分正确但有明显遗漏。",
    3: "大体正确，轻微遗漏或不精确。",
    4: "正确完整，仅有琐碎不精确。",
    5: "完全正确、完整，且符合问题要求的形式。",
}


def build_judge_prompt(
    *,
    probe_question: str,
    probe_type: str,
    reference_answer: str,
    continuation_text: str,
) -> str:
    dim_block = "\n".join(f"- {d}: {DIMENSION_DESCRIPTIONS[d]}" for d in DIMENSIONS)
    scale_block = "\n".join(f"  {k}: {v}" for k, v in sorted(SCORE_SCALE.items()))
    dim_schema = "\n".join(f'  "{d}": <0-5 integer>,' for d in DIMENSIONS)

    return f"""你是消息压缩评测裁判。助手仅基于**压缩后的会话上下文**回答了 probe 问题。
请先给 recall 判分，再按五个诊断维度打分。

recall 判分（headline，三档）：
  2: 回答正确且覆盖 reference 的关键事实。
  1: 部分正确：关键事实有遗漏或不精确，但方向正确。
  0: 错误、臆造或答非所问。

诊断维度说明：
{dim_block}

诊断维度 0–5 量表：
{scale_block}

PROBE TYPE: {probe_type}

PROBE QUESTION:
{probe_question}

REFERENCE ANSWER（评分锚点，assistant 答案应与之对齐）:
{reference_answer}

ASSISTANT ANSWER（待评分）:
{continuation_text}

仅输出 JSON，不要其它文字：
{{
  "recall": <2|1|0>,
{dim_schema}
  "notes": "一句话说明主要扣分点（可空）"
}}"""


def parse_judge_response(raw: str) -> Dict[str, Any]:
    if not raw or not raw.strip():
        raise ValueError("empty judge response")

    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence:
        text = fence.group(1).strip()
    brace = re.search(r"\{[\s\S]*\}", text)
    if not brace:
        raise ValueError(f"no JSON object in judge response: {raw[:200]!r}")
    parsed = json.loads(brace.group(0))

    if "recall" not in parsed:
        raise ValueError("missing recall verdict")
    recall = int(round(float(parsed["recall"])))
    if recall not in RECALL_VALUES:
        raise ValueError(f"recall out of range: {recall}")

    scores: Dict[str, int] = {}
    for dim in DIMENSIONS:
        if dim not in parsed:
            raise ValueError(f"missing dimension {dim}")
        val = int(round(float(parsed[dim])))
        if val < 0 or val > 5:
            raise ValueError(f"{dim} out of range: {val}")
        scores[dim] = val

    notes = str(parsed.get("notes") or "")[:200]
    overall = sum(scores.values()) / len(scores)
    return {
        "recall": recall,
        "scores": scores,
        "notes": notes,
        "overall_probe_score": round(overall, 4),
    }
