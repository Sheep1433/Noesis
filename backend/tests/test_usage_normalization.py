"""Provider usage normalization contracts (task 3.1).

Covers multi-form usage normalization: LangChain UsageMetadata details,
Anthropic top-level cache fields, OpenAI/OpenRouter prompt_tokens aliases,
and the rule that details are preserved but NOT zero-filled or double-counted.
"""

from __future__ import annotations

from typing import Any, Dict

from noesis_server.domain.chat.streaming.langgraph_sse import (
    _accumulate_detail,
    _extract_input_token_details,
    _extract_output_token_details,
    _normalize_usage,
)


def test_normalize_usage_langchain_form_with_cache_and_reasoning() -> None:
    """LangChain UsageMetadata：input_tokens 含 cache，details 为子项，保留不丢。"""
    raw = {
        "input_tokens": 350,
        "output_tokens": 240,
        "total_tokens": 590,
        "input_token_details": {"cache_read": 100, "cache_creation": 200},
        "output_token_details": {"reasoning": 50},
    }
    out = _normalize_usage(raw)
    assert out["input_tokens"] == 350
    assert out["output_tokens"] == 240
    assert out["total_tokens"] == 590
    assert out["input_token_details"] == {"cache_read": 100, "cache_write": 200}
    assert out["output_token_details"] == {"reasoning": 50}


def test_normalize_usage_anthropic_top_level_cache_fields() -> None:
    """Anthropic 顶层字段（OpenRouter 代理可能只暴露这些）：cache 从顶层回退提取。"""
    raw = {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_input_tokens": 60,
        "cache_creation_input_tokens": 0,
    }
    out = _normalize_usage(raw)
    assert out["input_tokens"] == 100
    assert out["input_token_details"]["cache_read"] == 60
    assert out["input_token_details"]["cache_write"] == 0


def test_normalize_usage_openai_prompt_tokens_alias() -> None:
    """OpenAI/OpenRouter 别名：prompt_tokens/completion_tokens 映射到 input/output。"""
    raw = {
        "prompt_tokens": 1500,
        "completion_tokens": 300,
        "total_tokens": 1800,
        "prompt_tokens_details": {"cached_tokens": 800},
    }
    out = _normalize_usage(raw)
    assert out["input_tokens"] == 1500
    assert out["output_tokens"] == 300
    assert out["total_tokens"] == 1800


def test_normalize_usage_missing_details_not_zero_filled() -> None:
    """Provider 只返回基础 usage：不附加 details，区分 '不支持' 与 '返回 0'。"""
    raw = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
    out = _normalize_usage(raw)
    assert out["input_tokens"] == 100
    assert "input_token_details" not in out
    assert "output_token_details" not in out


def test_normalize_usage_empty_and_none() -> None:
    assert _normalize_usage(None) == {}
    assert _normalize_usage({}) == {}
    assert _normalize_usage("not a dict") == {}


def test_normalize_usage_object_with_attributes() -> None:
    """非 dict 对象（LangChain TypedDict 实际是 dict，但防御对象形态）。"""
    from types import SimpleNamespace

    raw = SimpleNamespace(
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
        input_token_details={"cache_read": 30},
    )
    out = _normalize_usage(raw)
    assert out["input_tokens"] == 100
    assert out["input_token_details"]["cache_read"] == 30


def test_normalize_usage_total_derived_when_missing() -> None:
    """total 缺失时由 input+output 推导。"""
    raw = {"input_tokens": 100, "output_tokens": 20}
    out = _normalize_usage(raw)
    assert out["total_tokens"] == 120


def test_extract_input_token_details_prefers_langchain_then_anthropic() -> None:
    """LangChain input_token_details 优先；缺时回退 Anthropic 顶层。"""
    d1 = {"input_token_details": {"cache_read": 50, "cache_creation": 10}}
    assert _extract_input_token_details(d1) == {"cache_read": 50, "cache_write": 10}

    d2 = {"cache_read_input_tokens": 60, "cache_creation_input_tokens": 5}
    assert _extract_input_token_details(d2) == {"cache_read": 60, "cache_write": 5}

    # 两者都在时 LangChain 优先
    d3 = {
        "input_token_details": {"cache_read": 50},
        "cache_read_input_tokens": 999,  # 应被忽略
    }
    assert _extract_input_token_details(d3) == {"cache_read": 50}


def test_extract_output_token_details_reasoning() -> None:
    d = {"output_token_details": {"reasoning": 200, "audio": 10}}
    out = _extract_output_token_details(d)
    assert out == {"reasoning": 200}


def test_accumulate_detail_sums_subkeys() -> None:
    """detail 子项累计：同子项求和，新子项初始化，缺失不补零。"""
    cum: Dict[str, Any] = {}
    _accumulate_detail(cum, "input_token_details", {"cache_read": 100, "cache_write": 50})
    _accumulate_detail(cum, "input_token_details", {"cache_read": 60})
    assert cum["input_token_details"] == {"cache_read": 160, "cache_write": 50}

    # None 不影响
    _accumulate_detail(cum, "input_token_details", None)
    assert cum["input_token_details"] == {"cache_read": 160, "cache_write": 50}

    # 新 detail 桶
    _accumulate_detail(cum, "output_token_details", {"reasoning": 80})
    assert cum["output_token_details"] == {"reasoning": 80}


def test_normalize_usage_does_not_double_count_details_in_total() -> None:
    """detail 不参与 total 二次相加：total 始终等于平铺 input+output（或 provider 给的值）。"""
    raw = {
        "input_tokens": 350,  # 已含 cache（LangChain 语义）
        "output_tokens": 240,
        "total_tokens": 590,
        "input_token_details": {"cache_read": 100, "cache_creation": 200},
    }
    out = _normalize_usage(raw)
    # total 不因 detail 再次相加
    assert out["total_tokens"] == 590
    assert out["total_tokens"] == out["input_tokens"] + out["output_tokens"]
