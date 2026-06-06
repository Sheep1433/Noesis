"""评测 dataset item 字段解析（与 Agent state 对齐）。"""

from __future__ import annotations

from typing import Any, Dict, List


def item_scenario_query(item: Dict[str, Any]) -> str:
    """用户补充说明：优先 scenario_description，兼容 query。"""
    for key in ("scenario_description", "query"):
        val = item.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def collect_all_point_names(scenes_testpoints: List[Dict[str, Any]]) -> List[str]:
    """收集阶段 A 全部非空 point_name（评测全量采纳，不模拟用户子集）。"""
    names: List[str] = []
    seen: set[str] = set()
    for scene in scenes_testpoints or []:
        for tp in scene.get("test_points") or []:
            n = str(tp.get("point_name") or "").strip()
            if n and n not in seen:
                seen.add(n)
                names.append(n)
    return names
