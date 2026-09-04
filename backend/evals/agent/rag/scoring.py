"""Deterministic scoring for Agentic RAG tool outputs."""

from __future__ import annotations

import json
from typing import Any, Iterable


def retrieved_sources(tool_outputs: Iterable[dict[str, Any]]) -> set[str]:
    """从 search_knowledge_base 工具输出提取来源文件名。

    工具载荷契约是 ``{"results": [{"file_name": ...}, ...]}``（kb_search_tool._format_hits）。
    """
    sources: set[str] = set()
    for item in tool_outputs:
        if item.get("name") != "search_knowledge_base":
            continue
        raw = item.get("output")
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        for hit in payload.get("results") or []:
            if isinstance(hit, dict) and str(hit.get("file_name") or "").strip():
                sources.add(str(hit["file_name"]).strip())
    return sources


def score_expected_sources(
    tool_outputs: Iterable[dict[str, Any]], expected_sources: Iterable[str]
) -> dict[str, Any]:
    expected = {str(name).strip() for name in expected_sources if str(name).strip()}
    retrieved = retrieved_sources(tool_outputs)
    matched = expected & retrieved
    recall = len(matched) / len(expected) if expected else 1.0
    return {
        "expected_sources": sorted(expected),
        "retrieved_sources": sorted(retrieved),
        "matched_sources": sorted(matched),
        "source_recall": round(recall, 4),
    }
