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


def to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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


def accumulate_detail(cum: Dict[str, Any], key: str, incoming: Dict[str, int] | None) -> None:
    """Accumulate detail sub-keys (cache_read/cache_write/reasoning) into a snapshot.

    Missing sub-keys are not zero-filled; existing values sum, absent values
    initialize. Details never participate in total_tokens re-summing.
    """
    if not incoming:
        return
    bucket = cum.get(key)
    if not isinstance(bucket, dict):
        bucket = {}
        cum[key] = bucket
    for sub_key, value in incoming.items():
        if value is None:
            continue
        bucket[sub_key] = bucket.get(sub_key, 0) + int(value)
