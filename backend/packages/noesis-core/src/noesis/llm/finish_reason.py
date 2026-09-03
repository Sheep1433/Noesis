"""Provider finish_reason 词表与归一化。

OpenAI 兼容网关（OpenCode/kilo 一族）会在多个流式 chunk 上重复携带
finish_reason，LangChain 聚合 chunk 时对同名 string 键做 ``+=`` 拼接
（``merge_dicts``），落库值变成 ``tool_callstool_calls`` / ``stopstop``。
若不归一，``length`` 截断会被拼成 ``lengthlength`` 而漏过截断判定，
被当成正常收尾。本模块是 provider finish_reason 的唯一解析点：
词表、管道映射与拼接归一都在这里，各消费方（bridge 遥测 / 截断终态 /
middleware 告警）只允许引用，不各自重判。
"""
from __future__ import annotations

from typing import Any, Dict

# provider finish_reason → 管道终止词汇：只纠偏截断/安全两类「模型没收尾完」
# 的信号（length/max_tokens/content_filter），其余变体（end_turn/tool_use 等）
# 属正常收尾，保持管道值。
PROVIDER_FINISH_REMAP: Dict[str, str] = {
    "length": "length_stop",
    "max_tokens": "length_stop",
    "content_filter": "safety_stop",
}

# 已知 provider finish_reason 全集；拼接归一只折叠这些 token 的重复
# （非重复值原样返回，不做猜测式改写）。
_KNOWN_FINISH_REASONS = (
    "stop",
    "length",
    "max_tokens",
    "content_filter",
    "function_call",
    "tool_calls",
    "tool_use",
    "end_turn",
    "stop_sequence",
)


def normalize_provider_finish_reason(raw: Any) -> str:
    """折叠网关重复 chunk 造成的同值拼接；未知值原样返回。"""
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        return ""
    for token in _KNOWN_FINISH_REASONS:
        count, remainder = divmod(len(text), len(token))
        if count >= 1 and remainder == 0 and text == token * count:
            return token
    return text
