"""判卷：recall 三档（2/1/0）+ 五维诊断，judge 与被测模型分离。

作答由真 Agent 路径完成（``evals.compression.agent_path``），本模块只负责
对落盘的作答文本打分；解析失败重试一次，仍失败标记 invalid（recall=None，
从分母剔除）。
"""

from __future__ import annotations

from typing import Any, Dict, Protocol

from evals.compression.rubric import DIMENSIONS, build_judge_prompt, parse_judge_response


class SupportsInvoke(Protocol):
    def invoke(self, prompt: Any) -> Any: ...


def grade_probe(
    *,
    probe_question: str,
    probe_type: str,
    reference_answer: str,
    continuation_text: str,
    llm: SupportsInvoke,
) -> Dict[str, Any]:
    """判卷：解析失败重试一次，仍失败标记 invalid（recall=None，从分母剔除）。"""
    prompt = build_judge_prompt(
        probe_question=probe_question,
        probe_type=probe_type,
        reference_answer=reference_answer,
        continuation_text=continuation_text,
    )
    raw = ""
    for _attempt in range(2):
        response = llm.invoke(prompt)
        raw = response.content if hasattr(response, "content") else str(response)
        try:
            parsed = parse_judge_response(str(raw or ""))
            parsed["judge_raw"] = raw
            parsed["parse_error"] = None
            return parsed
        except ValueError:
            continue
    return {
        "recall": None,
        "scores": {d: 0 for d in DIMENSIONS},
        "notes": "",
        "overall_probe_score": 0.0,
        "judge_raw": raw,
        "parse_error": "judge response unparseable after retry",
    }
