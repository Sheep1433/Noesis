"""检索来源的共享投影工具：工具输出 → retrieval part + 来源身份归一 + 跨边界登记。

主 run 桥接层与子 Agent 统一管道共用同一份「检索工具输出 → retrieval part」
构造（两条管线都经 LangGraphSseBridge 进入这里），保证检索维度的投影同构。

本模块还承载来源溯源的三块共享逻辑：
- canonical URL / 来源身份归一（前后端共享规则；前端见 canonicalUrl.ts，
  测试用例集两侧对齐）；
- 子会话投影 → 去重来源清单提取（终态通知 / check_task 携带）；
- 跨边界来源登记（子 Agent 清单 → 主 run 消息上带 origin 的 retrieval parts）。
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from noesis.chat.event_mapping.tool_payload import retrieval_payload
from noesis.runtime.logging import logger

# 检索类工具：输出被解析为结构化 retrieval parts（主 / 子管道同集合）
RETRIEVAL_TOOL_NAMES = frozenset({"search_knowledge_base", "web_search", "web_fetch"})

# 单子会话累计去重来源上界（任务级清单；完整数据以子会话落库 parts 为准）
MAX_TASK_SOURCES = 200
# 跨边界登记上界：主会话登记量按「弧级聚合展示所需」约束（去重头部）
MAX_CROSS_BOUNDARY_SOURCES = 50
# 注入文本（通知 / check_task）中来源附录的条数与总字符上界
SOURCES_APPENDIX_MAX_ITEMS = 30
SOURCES_APPENDIX_MAX_CHARS = 2000

# tracking 参数：utm_* 前缀 + 已知点击归因参数
_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAMS = frozenset({
    "fbclid", "gclid", "dclid", "msclkid", "igshid", "mc_cid", "mc_eid",
    "spm", "scm", "yclid", "twclid", "_hsenc", "_hsmi", "vero_id", "wickedid",
})


def _is_tracking_param(name: str) -> bool:
    lowered = name.lower()
    return lowered in _TRACKING_PARAMS or lowered.startswith(_TRACKING_PARAM_PREFIXES)


def canonical_url(raw: str) -> str:
    """来源身份归一：去 tracking 参数、协议/host 归一（http→https、小写、
    去默认端口）、去 fragment、query 参数排序、去尾部冗余分隔符。

    前端 canonicalUrl.ts 实现同一规则（用例集见两侧测试，须保持对齐）。
    """
    value = str(raw or "").strip()
    if not value:
        return ""
    split = urlsplit(value)
    if not split.netloc:
        split = urlsplit(f"https://{value}")
    scheme = split.scheme.lower()
    if scheme not in ("http", "https"):
        return value
    scheme = "https"
    host = (split.hostname or "").lower()
    if not host:
        return value
    port = split.port
    netloc = host
    if port and port not in (80, 443):
        netloc = f"{host}:{port}"
    path = split.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    query_pairs = sorted(
        (key, val)
        for key, val in parse_qsl(split.query, keep_blank_values=True)
        if not _is_tracking_param(key)
    )
    return urlunsplit((scheme, netloc, path, urlencode(query_pairs), ""))


def source_identity(result: Dict[str, Any]) -> str:
    """来源身份键：web 用 canonical URL，知识库用 collection + title。"""
    if str(result.get("source_type") or "") == "web":
        key = canonical_url(str(result.get("url") or ""))
        if key:
            return f"web:{key}"
        return f"web:{result.get('evidence_id')}"
    return f"kb:{result.get('collection_name') or ''}:{result.get('title') or ''}"


def register_tool_retrieval(
    builder: Any,
    tool_name: str,
    tool_call_id: str,
    clean_output: str,
) -> Optional[Any]:
    """检索类工具输出 → retrieval part（主 / 子管道共用的唯一构造点）。

    非检索工具或输出不可解析时返回 None。工具 part 的展示输出同步替换为
    「检索到 N 条来源」摘要（原始结果进 retrieval part 持久化）。
    """
    if tool_name not in RETRIEVAL_TOOL_NAMES:
        return None
    parsed = retrieval_payload(clean_output)
    if parsed is None:
        return None
    tool_part = builder.get_tool(tool_call_id)
    tool_input = tool_part.arguments if tool_part is not None else {}
    part = builder.register_retrieval_results(
        tool_call_id=tool_call_id,
        query=str((tool_input or {}).get("query") or (tool_input or {}).get("url") or ""),
        results=parsed["results"],
        truncated=bool(parsed.get("truncated")),
    )
    if tool_part is not None:
        tool_part.output = f"检索到 {len(part.results)} 条来源"
    return part


def extract_deduped_sources(
    content: Dict[str, Any],
    *,
    limit: int = MAX_TASK_SOURCES,
) -> List[Dict[str, Any]]:
    """从消息内容 parts 提取去重来源清单（按来源身份去重、保序、有界）。"""
    out: Dict[str, Dict[str, Any]] = {}
    for part in content.get("parts") or []:
        if not isinstance(part, dict) or part.get("type") != "retrieval":
            continue
        for item in part.get("results") or []:
            if not isinstance(item, dict):
                continue
            key = source_identity(item)
            if key and key not in out:
                out[key] = item
    return list(out.values())[:limit]


def format_sources_appendix(sources: List[Dict[str, Any]]) -> str:
    """来源清单文本段（通知注入 / check_task 返回共用）：有界；无来源返回空。"""
    items = [s for s in sources if isinstance(s, dict)]
    if not items:
        return ""
    lines: List[str] = []
    for index, item in enumerate(items[:SOURCES_APPENDIX_MAX_ITEMS], 1):
        title = str(item.get("title") or "").strip() or str(item.get("url") or "未命名来源")
        url = str(item.get("url") or "").strip()
        lines.append(f"{index}. {title}" + (f" — {url}" if url else ""))
    block = f"检索来源（去重后 {len(items)} 条）：\n" + "\n".join(lines)
    if len(items) > SOURCES_APPENDIX_MAX_ITEMS:
        block += (
            f"\n…（共 {len(items)} 条，仅列前 {SOURCES_APPENDIX_MAX_ITEMS} 条，"
            "完整来源见子会话详情）"
        )
    return block[:SOURCES_APPENDIX_MAX_CHARS]


# ---------------------------------------------------------------------------
# 跨边界来源登记：子 Agent 终态清单 → 主 run assistant 消息上的 retrieval parts
# ---------------------------------------------------------------------------

_PENDING_LOCK = threading.Lock()
# session_id -> [{"label": 任务标题, "sources": [result dict, ...]}]
_PENDING: Dict[str, List[Dict[str, Any]]] = {}


def register_pending_sources(
    session_id: str,
    label: str,
    sources: List[Dict[str, Any]],
) -> None:
    """登记一份待写入主会话的跨边界来源（通知注入与 check_task 两条通道共用）。

    主 run 桥接层在 finish 前统一 drain（见 register_cross_boundary_sources）。
    同一 (label, 来源身份) 重复登记合并——同任务被多次 check、或通知与
    check 双通道送达时不清单不翻倍。
    """
    if not session_id or not sources:
        return
    clean_label = str(label or "").strip()
    with _PENDING_LOCK:
        entries = _PENDING.setdefault(session_id, [])
        target = next((e for e in entries if e.get("label") == clean_label), None)
        if target is None:
            entries.append({
                "label": clean_label,
                "sources": list(sources[:MAX_CROSS_BOUNDARY_SOURCES]),
            })
            return
        seen = {source_identity(s) for s in target["sources"]}
        for item in sources:
            key = source_identity(item)
            if not key or key in seen:
                continue
            if len(target["sources"]) >= MAX_CROSS_BOUNDARY_SOURCES:
                break
            seen.add(key)
            target["sources"].append(item)


def drain_pending_sources(session_id: str) -> List[Dict[str, Any]]:
    """取走并清空该会话的待登记来源（桥接层 finish 时调用，一次性）。"""
    if not session_id:
        return []
    with _PENDING_LOCK:
        return _PENDING.pop(session_id, [])


def register_cross_boundary_sources(builder: Any, session_id: str) -> int:
    """主 run 收尾：drain 跨边界来源，登记为带 origin 标记的 retrieval parts。

    数据落位与展示位置解耦：收取消息（通常是过程消息）落库来源数据，
    展示由前端按研究弧聚合。返回登记的来源条数。
    """
    entries = drain_pending_sources(session_id)
    if not entries:
        return 0
    count = 0
    for entry in entries:
        label = str(entry.get("label") or "").strip()
        sources = [s for s in entry.get("sources") or [] if isinstance(s, dict)]
        if not sources:
            continue
        part = builder.register_retrieval_results(
            tool_call_id=f"subagent-sources-{uuid.uuid4().hex[:12]}",
            query=label,
            results=sources,
            truncated=len(sources) >= MAX_CROSS_BOUNDARY_SOURCES,
            origin={"kind": "subagent", "label": label},
        )
        count += len(part.results)
    if count:
        logger.info(
            "cross boundary sources registered session_id={} entries={} sources={}",
            session_id,
            len(entries),
            count,
        )
    return count


__all__ = [
    "MAX_CROSS_BOUNDARY_SOURCES",
    "MAX_TASK_SOURCES",
    "RETRIEVAL_TOOL_NAMES",
    "SOURCES_APPENDIX_MAX_CHARS",
    "SOURCES_APPENDIX_MAX_ITEMS",
    "canonical_url",
    "drain_pending_sources",
    "extract_deduped_sources",
    "format_sources_appendix",
    "register_cross_boundary_sources",
    "register_pending_sources",
    "register_tool_retrieval",
    "source_identity",
]
