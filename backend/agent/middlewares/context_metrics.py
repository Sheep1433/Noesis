"""Shared context window metrics for summarization and UI display."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.summarization import TokenCounter, count_tokens_approximately

from config.env import ModelConfig
from llm import get_llm
from utils.log_util import logger

DEFAULT_CONTEXT_MAX_INPUT_TOKENS = 128_000


def get_agent_token_counter() -> TokenCounter:
    return count_tokens_approximately


def resolve_context_max_tokens() -> int:
    if ModelConfig.context_max_input_tokens > 0:
        return int(ModelConfig.context_max_input_tokens)
    if ModelConfig.summarization_max_input_tokens > 0:
        logger.warning(
            "summarization.max_input_tokens 已废弃，请改用 context.max_input_tokens"
        )
        return int(ModelConfig.summarization_max_input_tokens)

    model = get_llm()
    profile = getattr(model, "profile", None) or {}
    profile_max = profile.get("max_input_tokens")
    if profile_max is not None:
        try:
            parsed = int(profile_max)
            if parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass

    logger.warning(
        "context.max_input_tokens 未配置，使用默认值 {}",
        DEFAULT_CONTEXT_MAX_INPUT_TOKENS,
    )
    return DEFAULT_CONTEXT_MAX_INPUT_TOKENS


def build_context_snapshot(messages: list[Any]) -> dict[str, int]:
    current_tokens = int(get_agent_token_counter()(messages))
    max_tokens = resolve_context_max_tokens()
    if max_tokens <= 0:
        used_percentage = 0
    else:
        used_percentage = min(100, round(current_tokens / max_tokens * 100))
    return {
        "current_tokens": current_tokens,
        "max_tokens": max_tokens,
        "used_percentage": used_percentage,
    }
