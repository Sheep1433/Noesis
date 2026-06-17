"""嵌入层：文本 → 向量。"""
from __future__ import annotations

import os

from config.env import ModelConfig


def get_embedding(model: str | None = None):
    """
    返回平台配置的 LangChain Embeddings 实例。

    模型名与 base_url 读 embedding 配置段；api_key 读 EMBEDDING_MODEL_API_KEY。
    """
    from langchain_openai import OpenAIEmbeddings

    model_name = (model or ModelConfig.embedding_model_name or "text-embedding-v4").strip()
    api_key = (
        ModelConfig.embedding_model_api_key
        or os.getenv("DASHSCOPE_API_KEY")
        or ""
    ).strip()
    base_url = (ModelConfig.embedding_model_base_url or "").strip()
    return OpenAIEmbeddings(
        model=model_name,
        openai_api_key=api_key,
        openai_api_base=base_url or None,
    )
