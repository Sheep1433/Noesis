"""从环境变量同步 HTTP 代理与 SSL 校验（供 httpx / OpenAI 客户端使用）。"""

from __future__ import annotations

import os
from typing import Any

import httpx


def set_proxy() -> None:
    """确保大小写代理环境变量成对存在，便于下游 HTTP 库读取。"""
    pairs = (
        ("HTTP_PROXY", "http_proxy"),
        ("HTTPS_PROXY", "https_proxy"),
        ("NO_PROXY", "no_proxy"),
    )
    for upper, lower in pairs:
        value = os.environ.get(upper) or os.environ.get(lower)
        if not value:
            continue
        os.environ.setdefault(upper, value)
        os.environ.setdefault(lower, value)


def http_ssl_verify() -> bool:
    """是否校验 HTTPS 证书；HTTP_SSL_VERIFY=false 时关闭（内网自签场景）。"""
    raw = os.getenv("HTTP_SSL_VERIFY") or os.getenv("SSL_VERIFY")
    if raw is None:
        return True
    return raw.strip().lower() in ("true", "1", "yes")


def httpx_client_kwargs(**extra: Any) -> dict[str, Any]:
    set_proxy()
    return {"verify": http_ssl_verify(), **extra}


def create_sync_client(**kwargs: Any) -> httpx.Client:
    return httpx.Client(**httpx_client_kwargs(**kwargs))


def create_async_client(**kwargs: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(**httpx_client_kwargs(**kwargs))
