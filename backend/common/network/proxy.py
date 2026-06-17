"""从环境变量同步 HTTP 代理（供 httpx / OpenAI 客户端使用）。"""

from __future__ import annotations

import os


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
