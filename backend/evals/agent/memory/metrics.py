"""记忆召回评测指标：条目级 recall@k / precision@k + 三层汇总（纯函数）。"""

from __future__ import annotations

import json
from typing import Any, Iterable

from evals.agent.rag.judge import VERDICT_SCORES


def parse_search_memory_slugs(tool_outputs: Iterable[dict[str, Any]]) -> list[str]:
    """从 search_memory 工具输出按序提取返回条目 slug。"""
    slugs: list[str] = []
    for item in tool_outputs:
        if item.get("name") != "search_memory":
            continue
        raw = item.get("output")
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        for hit in payload.get("results") or []:
            if isinstance(hit, dict) and str(hit.get("slug") or "").strip():
                slugs.append(str(hit["slug"]).strip())
    return slugs


def memory_accessed(record: dict[str, Any]) -> bool:
    """行为级记忆访问：主动调 search_memory，或经 /memory 虚拟路径读取（read_file/ls 等）。

    tool_outputs 的 input 为工具入参原始值（dict 或 str），路径判定做字符串包含。
    """
    if record.get("search_memory_calls"):
        return True
    for item in record.get("tool_outputs") or []:
        if item.get("name") == "search_memory":
            return True
        tool_input = item.get("input")
        if tool_input is not None and "/memory" in str(tool_input):
            return True
    return False


def retrieval_scores(
    returned_slugs: list[str], answer_session_ids: list[str], *, k: int = 5
) -> dict[str, Any]:
    """条目级 recall@k / precision@k：期望集 = answer_session_ids，检索集 = 前 k 条返回。"""
    expected = set(answer_session_ids)
    retrieved = list(dict.fromkeys(returned_slugs[:k]))
    if not expected:
        return {"recall@k": None, "precision@k": None, "retrieved": retrieved}
    hit = [s for s in retrieved if s in expected]
    return {
        "recall@k": len(hit) / len(expected),
        "precision@k": len(hit) / len(retrieved) if retrieved else 0.0,
        "retrieved": retrieved,
    }


def classify_run_error(record: dict[str, Any]) -> str | None:
    """未完成样本的错误归类：timeout（预算耗尽）/ agent_error / incomplete。

    timeout 是预算问题不是链路回归，与 agent_error 分开计数，基线对比时
    才能区分「跑不完」和「跑坏了」。
    """
    if record.get("completed"):
        return None
    error = str(record.get("error") or "")
    if error.startswith("timeout"):
        return "timeout"
    if error:
        return "agent_error"
    return "incomplete"


def summarize_memory_eval(records: list[dict[str, Any]]) -> dict[str, Any]:
    """三层汇总：答案正确性（judge verdict）、检索命中、行为级召回 + 负例误召回。"""
    n = len(records)
    positives = [r for r in records if not r.get("negative")]
    negatives = [r for r in records if r.get("negative")]
    judged = [r for r in positives
              if (r.get("judge") or {}).get("verdict") in VERDICT_SCORES]
    judge_invalid = [r for r in positives
                     if (r.get("judge") or {}).get("verdict") == "invalid"]
    recalls = [r["retrieval"]["recall@k"] for r in positives
               if r.get("retrieval", {}).get("recall@k") is not None]
    precisions = [r["retrieval"]["precision@k"] for r in positives
                  if r.get("retrieval", {}).get("precision@k") is not None]
    error_kinds = [k for k in (classify_run_error(r) for r in records) if k]

    def rate(num: float, den: int) -> float:
        return round(num / den, 4) if den else 0.0

    by_type: dict[str, dict[str, int]] = {}
    for r in judged:
        slot = by_type.setdefault(r.get("question_type") or "unknown", {"n": 0, "accepted": 0})
        slot["n"] += 1
        slot["accepted"] += 1 if r["judge"]["verdict"] == "accepted" else 0

    return {
        "samples": n,
        "positives": len(positives),
        "negatives": len(negatives),
        "errors": len(error_kinds),
        "error_breakdown": {
            kind: error_kinds.count(kind)
            for kind in ("timeout", "agent_error", "incomplete")
            if error_kinds.count(kind)
        },
        # 层 1：答案正确性（对齐 LongMemEval 协议口径的判卷）；折半口径
        # partial 计 0.5，与 rag 评测线一致——小样本下档位摆动的缓冲读数
        "answer_accepted_rate": rate(
            sum(1 for r in judged if r["judge"]["verdict"] == "accepted"), len(judged)),
        "answer_score_rate_half": rate(
            sum(VERDICT_SCORES[r["judge"]["verdict"]] for r in judged), len(judged)),
        "judged": len(judged),
        "judge_invalid": len(judge_invalid),
        # 层 2：检索命中（条目级）
        "mean_recall@k": round(sum(recalls) / len(recalls), 4) if recalls else None,
        "mean_precision@k": round(sum(precisions) / len(precisions), 4) if precisions else None,
        # 层 3：行为级召回（主动访问记忆：search_memory 或 /memory 路径读取）
        "behavior_recall_rate": rate(
            sum(1 for r in positives if memory_accessed(r)), len(positives)),
        # 负例
        "negative_false_recall_rate": rate(
            sum(1 for r in negatives if r.get("violation")), len(negatives)),
        "by_question_type": by_type,
    }
