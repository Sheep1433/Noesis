"""嵌入层：文本 → 向量。"""
from __future__ import annotations

from config.env import ModelConfig


def is_embedding_configured() -> bool:
    """Embedding 须独立配置 name / base_url / api_key，不回退主模型。"""
    return bool(
        (ModelConfig.embedding_model_name or "").strip()
        and (ModelConfig.embedding_model_base_url or "").strip()
        and (ModelConfig.embedding_model_api_key or "").strip()
    )


def is_vlm_configured() -> bool:
    """VLM 须配置 name / base_url / api_key；api_key 未单独设置时可回退 EMBEDDING_MODEL_API_KEY。"""
    return bool(
        (ModelConfig.vlm_model_name or "").strip()
        and (ModelConfig.vlm_model_base_url or "").strip()
        and (ModelConfig.vlm_model_api_key or "").strip()
    )


def embedding_not_configured_message() -> str:
    return (
        "未配置 Embedding 模型：请在 config.yaml 设置 embedding.name 与 embedding.base_url，"
        "并在 .env 设置 EMBEDDING_MODEL_API_KEY；否则无法生成向量入库。"
    )


def get_embedding(model: str | None = None):
    """
    返回平台配置的 LangChain Embeddings 实例。

    模型名与 base_url 读 embedding 配置段；api_key 读 EMBEDDING_MODEL_API_KEY。
    未完整配置时抛出 ValueError（不回退主模型或 DASHSCOPE_API_KEY）。

    DashScope 兼容接口只接受 str / list[str] 作为 input，不接受 tiktoken 整数序列；
    因此关闭 check_embedding_ctx_length。text-embedding-v4 单请求 batch 上限为 10。
    """
    if not is_embedding_configured():
        raise ValueError(embedding_not_configured_message())

    from langchain_openai import OpenAIEmbeddings

    model_name = (model or ModelConfig.embedding_model_name).strip()
    api_key = ModelConfig.embedding_model_api_key.strip()
    base_url = ModelConfig.embedding_model_base_url.strip()
    kwargs: dict = {
        "model": model_name,
        "openai_api_key": api_key,
        "openai_api_base": base_url,
        "check_embedding_ctx_length": False,
    }
    if "dashscope" in base_url.lower():
        kwargs["chunk_size"] = 10
    return OpenAIEmbeddings(**kwargs)
