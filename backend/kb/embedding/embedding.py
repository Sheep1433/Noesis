"""嵌入层：文本 → 向量。"""
from __future__ import annotations

import os

from config.env import ModelConfig


def get_embedding(model: str | None = None):
    """
    返回平台配置的 LangChain Embeddings 实例。

    模型名默认读取 ModelConfig.embedding_model_name；具体后端由配置决定。
    """
    from langchain_community.embeddings import DashScopeEmbeddings

    model_name = (model or ModelConfig.embedding_model_name or "text-embedding-v4").strip()
    api_key = ModelConfig.embedding_model_api_key or os.getenv("DASHSCOPE_API_KEY") or None
    return DashScopeEmbeddings(dashscope_api_key=api_key, model=model_name)
