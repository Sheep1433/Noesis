"""可种植事实的合成 fixture：零 LLM 冒烟档。

固定种子生成带埋点事实的长对话（每 N 轮埋一个 deploy code），
配对生成对应 probe 题库。冒烟路径注入假 summarize/作答/判卷，
不依赖真实数据与 LLM 即可回归压缩评测的完整臂流程。
"""

from __future__ import annotations

import random
from typing import Any

N_TURNS = 40
FACT_EVERY = 8
SEED = 7


def make_synthetic_fixture(*, n_turns: int = N_TURNS, seed: int = SEED) -> dict[str, Any]:
    """确定性合成 fixture：每 FACT_EVERY 轮埋一个可断言事实。"""
    rng = random.Random(seed)
    messages: list[dict[str, Any]] = [
        {"type": "human", "content": "Work through the checklist and remember the deploy codes."},
    ]
    facts: list[str] = []
    for i in range(n_turns):
        fact = ""
        if i % FACT_EVERY == 0:
            code = f"Z{rng.randint(1000, 9999)}"
            fact = f" The deploy code for region {i // FACT_EVERY} is {code}."
            facts.append(f"The deploy code for region {i // FACT_EVERY} is {code}.")
        messages.append({"type": "ai", "content": f"Working on step {i}.{fact}"})
        messages.append({
            "type": "tool",
            "content": ("step output " * 40) + f"result-{i}",
            "tool_call_id": f"c{i}",
            "name": "terminal",
        })
    messages.append({"type": "ai", "content": "Checklist complete."})
    return {
        "id": "synthetic_smoke",
        "description": "可种植事实合成 fixture（零 LLM 冒烟档，seed=7）",
        "source": "synthetic",
        "compress_options": {"force": True},
        "messages": messages,
    }, facts


def make_synthetic_probes(facts: list[str]) -> dict[str, Any]:
    """事实 → recall probe 题库（reference_answer 即埋点事实）。"""
    return {
        "fixture_id": "synthetic_smoke",
        "transcript_sha256": None,
        "probes": [
            {
                "id": f"p{i}",
                "type": "recall",
                "question": f"What is the deploy code for region {i}?",
                "reference_answer": fact,
            }
            for i, fact in enumerate(facts)
        ],
    }


def fact_dropping_summarize(messages):  # pragma: no cover - 测试注入用
    """丢全部事实的假摘要（冒烟断言：recall 应为 0）。"""
    return "Summary: the user worked through a checklist. All details omitted."


def fact_keeping_summarize(messages):  # pragma: no cover - 测试注入用
    """保留全部埋点事实的假摘要（冒烟断言：recall 应为满分）。"""
    from evals.compression.synthetic import extract_planted_facts
    return "Summary:\n" + "\n".join(f"- {f}" for f in extract_planted_facts(messages))


def extract_planted_facts(messages) -> list[str]:
    """从消息序列中提取埋点事实（region 侧与摘要侧共用）。"""
    import re
    facts: list[str] = []
    for m in messages:
        content = getattr(m, "content", "") or ""
        for match in re.finditer(r"The deploy code for region (\d+) is (Z\d+)\.", str(content)):
            facts.append(f"The deploy code for region {match.group(1)} is {match.group(2)}.")
    return facts
