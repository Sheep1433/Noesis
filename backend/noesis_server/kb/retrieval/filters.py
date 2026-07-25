"""检索 filters 归一化：Qdrant 精确匹配 vs 应用层前缀过滤。"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def split_search_filters(
    filters: Optional[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    将 API filters 拆为：
    - qdrant_filter: 传给 VectorStore / Retrieval 的精确匹配 dict
    - post_filter: 应用层过滤（如 header_path_prefix）
    """
    if not filters:
        return None, {}

    raw = dict(filters)
    post: Dict[str, Any] = {}

    prefix = raw.pop("header_path_prefix", None)
    if prefix is not None and str(prefix).strip():
        post["header_path_prefix"] = str(prefix).strip()

    file_name_in = raw.pop("file_name_in", None)
    if isinstance(file_name_in, (list, tuple, set)):
        names = [str(x).strip() for x in file_name_in if str(x).strip()]
        if names:
            post["file_name_in"] = names

    exclude_file_names = raw.pop("exclude_file_names", None)
    if isinstance(exclude_file_names, (list, tuple, set)):
        excluded = [str(x).strip() for x in exclude_file_names if str(x).strip()]
        if excluded:
            post["exclude_file_names"] = excluded

    # 空值键忽略
    qdrant: Dict[str, Any] = {}
    for key, value in raw.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        qdrant[key] = value

    return (qdrant if qdrant else None), post


def document_matches_post_filter(metadata: Dict[str, Any], post_filter: Dict[str, Any]) -> bool:
    if not post_filter:
        return True

    prefix = post_filter.get("header_path_prefix")
    if prefix is not None:
        header_path = str(metadata.get("header_path") or "")
        if not header_path.startswith(str(prefix)):
            return False

    file_name = str(metadata.get("file_name") or metadata.get("source_name") or "")
    file_name_in = post_filter.get("file_name_in")
    if file_name_in is not None:
        allowed = {str(x).strip() for x in file_name_in if str(x).strip()}
        if allowed and file_name not in allowed:
            return False

    exclude_file_names = post_filter.get("exclude_file_names")
    if exclude_file_names is not None:
        excluded = {str(x).strip() for x in exclude_file_names if str(x).strip()}
        if file_name in excluded:
            return False

    return True
