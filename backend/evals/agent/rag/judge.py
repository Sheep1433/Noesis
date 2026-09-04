"""Agent E2E gold_answer 判卷：LLM-as-judge 三档（采纳 / 部分采纳 / 不采纳）。

judge 与被评模型分离（evals.manifest.require_judge_separation 在 CLI 层校验）；
prompt 版本进 manifest，改 prompt 即换版本号。
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

JUDGE_PROMPT_VERSION = "gold-answer-judge/v1"

VERDICTS = ("accepted", "partial", "rejected")
VERDICT_LABELS = {"accepted": "采纳", "partial": "部分采纳", "rejected": "不采纳"}
# 任务成功率两种口径：全量（仅采纳）与折半（部分采纳计 0.5）
VERDICT_SCORES = {"accepted": 1.0, "partial": 0.5, "rejected": 0.0}


class SupportsInvoke(Protocol):
    def invoke(self, prompt: list[Any]) -> Any: ...


def build_judge_prompt(*, question: str, gold_answer: str, answer: str) -> str:
    return f"""你是问答评测裁判，只依据 gold_answer 判定 assistant 回答的采纳档位。

判定标准：
- accepted（采纳）：回答覆盖 gold_answer 的全部关键事实，且无与 gold_answer 冲突的错误事实。
- partial（部分采纳）：回答包含部分关键事实，或存在明显但不颠覆的偏差/遗漏。
- rejected（不采纳）：关键事实缺失、错误，或答非所问。

QUESTION:
{question}

GOLD ANSWER（评分锚点）:
{gold_answer}

ASSISTANT ANSWER（待判卷）:
{answer}

仅输出 JSON，不要其它文字：
{{"verdict": "accepted" | "partial" | "rejected", "notes": "一句话说明主要扣分点（可空）"}}"""


def parse_judge_response(raw: str) -> dict[str, Any]:
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
    verdict = str(parsed.get("verdict") or "").strip().lower()
    if verdict not in VERDICTS:
        raise ValueError(f"invalid verdict: {verdict!r}")
    return {"verdict": verdict, "notes": str(parsed.get("notes") or "")[:200]}


def judge_answer(
    *,
    question: str,
    gold_answer: str,
    answer: str,
    llm: SupportsInvoke,
) -> dict[str, Any]:
    """判卷；解析失败重试一次，仍失败标记 invalid（不伪造分数）。"""
    prompt = build_judge_prompt(question=question, gold_answer=gold_answer, answer=answer)
    raw = ""
    for _attempt in range(2):
        raw = str(llm.invoke(prompt).content or "")
        try:
            parsed = parse_judge_response(raw)
            parsed["judge_raw"] = raw
            parsed["parse_error"] = None
            return parsed
        except ValueError:
            continue
    return {"verdict": "invalid", "notes": "", "judge_raw": raw,
            "parse_error": "judge response unparseable after retry"}
