"""LLM 集成：按 MODEL_TYPE 实例化 LangChain ChatModel。"""
from __future__ import annotations

from noesis.llm.factory import build_chat_model, get_llm

__all__ = ["build_chat_model", "get_llm"]
