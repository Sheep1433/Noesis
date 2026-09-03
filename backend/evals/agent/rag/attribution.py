"""E2E 失败归因：对判卷不采纳的题，按确定性规则归到三类 + 待人工复核。

规则（design D4）：
- 检索未命中 GT（组件层数据：kb 评测 raw 的同题 gt_rank，或现场补一次检索）→ 检索没召回
- 检索命中但未调用 KB 工具 / 调用了替代工具（web_search 顶替）→ 工具行为异常
- 检索命中且工具正常但回答错误 → 推理错
- 无组件层数据可关联 → 待人工复核（不默认归类）
"""

from __future__ import annotations

from typing import Any

CATEGORIES = ("retrieval_miss", "tool_anomaly", "reasoning_error", "manual_review")
CATEGORY_LABELS = {
    "retrieval_miss": "检索没召回",
    "tool_anomaly": "工具行为异常",
    "reasoning_error": "推理错",
    "manual_review": "待人工复核",
}


def attribute_record(record: dict[str, Any]) -> dict[str, Any]:
    """对单条不采纳记录分类。record 需含 judge verdict 与组件层线索字段。"""
    verdict = (record.get("judge") or {}).get("verdict")
    if verdict != "rejected":
        return {"category": None, "reason": "非不采纳题，不参与归因"}

    retrieval_hit = record.get("retrieval_hit")
    kb_tool_called = bool(record.get("kb_tool_called"))
    used_web_search = bool((record.get("tool_stats") or {}).get("web_search"))

    if retrieval_hit is False:
        return {"category": "retrieval_miss", "reason": "检索未命中 GT 文档"}
    if not kb_tool_called:
        return {
            "category": "tool_anomaly",
            "reason": "检索命中但未调用 search_knowledge_base"
            + ("（用了 web_search 顶替）" if used_web_search else ""),
        }
    if retrieval_hit is True:
        return {"category": "reasoning_error", "reason": "检索命中且工具正常，回答仍不采纳"}
    # retrieval_hit 未知（无组件层数据且未现场补检索）
    return {"category": "manual_review", "reason": "无检索命中数据，无法确定性归因"}


def attribute_failures(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {c: 0 for c in CATEGORIES}
    details = []
    for record in records:
        attributed = attribute_record(record)
        if attributed["category"] is None:
            continue
        counts[attributed["category"]] += 1
        details.append({
            "sample_id": record.get("sample_id"),
            "question_id": record.get("question_id"),
            **attributed,
        })
    return {"counts": counts, "details": details}


def render_attribution_md(attribution: dict[str, Any]) -> str:
    counts = attribution["counts"]
    total = sum(counts.values())
    lines = [
        "# Agent E2E 失败归因",
        "",
        f"- 不采纳题总数: {total}",
        "",
        "| 类别 | 计数 |",
        "|---|---:|",
    ]
    for cat in CATEGORIES:
        lines.append(f"| {CATEGORY_LABELS[cat]} | {counts[cat]} |")
    if attribution["details"]:
        lines.extend(["", "## 逐题明细", ""])
        for d in attribution["details"]:
            lines.append(f"- **{d['sample_id']}**（{d['question_id']}）→ {CATEGORY_LABELS[d['category']]}：{d['reason']}")
    return "\n".join(lines) + "\n"
