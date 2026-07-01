"""DashScope text-rerank 封装；未配置或失败时由调用方降级。"""
from __future__ import annotations

from typing import List, Sequence, Tuple

import httpx

from config.env import ModelConfig
from common.logging import logger

_RERANK_API_URL = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"


def is_rerank_available() -> bool:
    return bool(
        (ModelConfig.rerank_model_name or "").strip()
        and (ModelConfig.rerank_model_api_key or "").strip()
    )


def rerank_documents(
    query: str,
    documents: Sequence[str],
    *,
    top_n: int | None = None,
) -> List[Tuple[int, float]]:
    """
    对文档列表重排。

    Returns:
        [(原始下标, relevance_score), ...] 按 score 降序。
    """
    if not documents:
        return []

    if not is_rerank_available():
        raise RuntimeError("rerank 模型未配置")

    q = (query or "").strip()
    if not q:
        return [(i, 0.0) for i in range(len(documents))]

    payload = {
        "model": ModelConfig.rerank_model_name.strip(),
        "input": {
            "query": q,
            "documents": list(documents),
        },
        "parameters": {
            "return_documents": False,
            "top_n": top_n if top_n is not None else len(documents),
        },
    }
    headers = {
        "Authorization": f"Bearer {ModelConfig.rerank_model_api_key.strip()}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)) as client:
        resp = client.post(_RERANK_API_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    output = data.get("output") or {}
    results = output.get("results") or []
    ranked: List[Tuple[int, float]] = []
    for item in results:
        try:
            idx = int(item["index"])
            score = float(item.get("relevance_score") or item.get("score") or 0.0)
            ranked.append((idx, score))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(f"[rerank] 跳过无效结果项: {item!r} ({exc})")

    if not ranked:
        logger.warning("[rerank] API 返回空 results，回退原始顺序")
        return [(i, 0.0) for i in range(len(documents))]

    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked
