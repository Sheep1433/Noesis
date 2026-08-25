"""Provider usage normalization shared by the SSE bridge and attribution collector.

Multi-form normalization (task 3.1):
- LangChain ``UsageMetadata`` (dict, input_tokens includes cache, details as sub-keys)
- Anthropic top-level fields (cache_read_input_tokens / cache_creation_input_tokens)
- OpenAI/OpenRouter aliases (prompt_tokens / completion_tokens / prompt_tokens_details)

Details (cache_read/cache_write/reasoning) are preserved when present and NOT
zero-filled when absent (spec §4: distinguish "Provider returned 0" from
"unsupported"). Details never participate in total_tokens re-summing.
"""

from __future__ import annotations

from typing import Any, Dict


#: 会话/消息级 usage 统计的标准字段（stats registry、middleware、bridge 聚合、
#: DB 落库 merge、历史回填共用，新增字段只改这里）。
USAGE_FIELDS: tuple[str, ...] = (
    "turns",
    "steps",
    "llm_ms",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "uncached_input_tokens",
    "cache_metrics_available_calls",
    "cache_metrics_unavailable_calls",
    "ttft_ms",
)


def to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def compute_used_percentage(current_tokens: int, max_tokens: int) -> int:
    """占用百分比；有占用但四舍五入为 0 时显示 1%，避免圆环长期为 0%。"""
    if max_tokens <= 0 or current_tokens <= 0:
        return 0
    pct = round(current_tokens / max_tokens * 100)
    if pct == 0:
        return 1
    return min(100, pct)


def normalize_usage(raw: Any) -> Dict[str, Any]:
    """Normalize provider usage_metadata into a compatible token structure.

    Preserves flat ``input_tokens``/``output_tokens``/``total_tokens`` (existing
    contract) and attaches ``input_token_details``/``output_token_details`` when
    the provider supplies them. Missing details are omitted, not zero-filled.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        d = raw
    else:
        d = {k: getattr(raw, k, None) for k in (
            "input_tokens", "output_tokens", "total_tokens",
            "input_token_details", "output_token_details",
            "prompt_tokens", "completion_tokens",
            "cache_read_input_tokens", "cache_creation_input_tokens",
        ) if getattr(raw, k, None) is not None}
        if not d:
            return {}

    flat_map = {
        "input_tokens": ("input_tokens", "inputTokens", "prompt_tokens", "promptTokens"),
        "output_tokens": ("output_tokens", "outputTokens", "completion_tokens", "completionTokens"),
        "total_tokens": ("total_tokens", "totalTokens"),
    }
    out: Dict[str, Any] = {}
    for canonical, aliases in flat_map.items():
        for k in aliases:
            v = d.get(k)
            if v is not None:
                iv = to_int(v)
                if iv is not None:
                    out[canonical] = iv
                break
    if "total_tokens" not in out and "input_tokens" in out and "output_tokens" in out:
        out["total_tokens"] = out["input_tokens"] + out["output_tokens"]

    input_details = extract_input_token_details(d)
    if input_details:
        out["input_token_details"] = input_details
        if "input_tokens" in out:
            out["uncached_input_tokens"] = max(
                0,
                out["input_tokens"]
                - int(input_details.get("cache_read") or 0)
                - int(input_details.get("cache_write") or 0),
            )
        out["cache_metrics_available"] = True
    elif "input_tokens" in out:
        out["cache_metrics_available"] = False
    output_details = extract_output_token_details(d)
    if output_details:
        out["output_token_details"] = output_details
    return out


def extract_input_token_details(d: Dict[str, Any]) -> Dict[str, int]:
    """Extract cache_read / cache_write from LangChain details or Anthropic top-level.

    Returns only keys that are present and non-None; does NOT zero-fill.
    """
    out: Dict[str, int] = {}
    details = d.get("input_token_details")
    if isinstance(details, dict):
        for src_key, canon in (("cache_read", "cache_read"), ("cache_creation", "cache_write"), ("cache_write", "cache_write")):
            v = to_int(details.get(src_key))
            if v is not None and canon not in out:
                out[canon] = v
    # OpenAI / OpenAI-compatible (opencode, tokenrhythm, …) 把缓存命中放在
    # prompt_tokens_details.cached_tokens。LangChain 不一定归一到 input_token_details，
    # 故在此显式回退提取，保证 stats 与 context 圆环都能拿到 cache_read。
    if "cache_read" not in out:
        ptd = d.get("prompt_tokens_details")
        if isinstance(ptd, dict):
            v = to_int(ptd.get("cached_tokens"))
            if v is not None:
                out["cache_read"] = v
    if "cache_read" not in out:
        v = to_int(d.get("cache_read_input_tokens"))
        if v is not None:
            out["cache_read"] = v
    if "cache_write" not in out:
        v = to_int(d.get("cache_creation_input_tokens"))
        if v is not None:
            out["cache_write"] = v
    return out


def extract_output_token_details(d: Dict[str, Any]) -> Dict[str, int]:
    """Extract reasoning output tokens from LangChain output_token_details."""
    out: Dict[str, int] = {}
    details = d.get("output_token_details")
    if isinstance(details, dict):
        v = to_int(details.get("reasoning"))
        if v is not None:
            out["reasoning"] = v
    return out
