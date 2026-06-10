"""web_fetch URL 校验与基础 SSRF 防护。"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
    }
)


def _is_private_ip(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
    )


def validate_fetch_url(url: str) -> tuple[bool, str]:
    """校验 URL 是否允许抓取。返回 (ok, error_message)。"""
    raw = (url or "").strip()
    if not raw:
        return False, "URL 不能为空"

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False, f"不支持的 URL scheme: {scheme or '(空)'}"

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return False, "URL 缺少主机名"

    if hostname in _BLOCKED_HOSTNAMES:
        return False, f"禁止访问的主机: {hostname}"

    # 字面量 IP（域名跳过，由下方 DNS 检查）
    if _is_private_ip(hostname):
        return False, f"禁止访问私有或保留地址: {hostname}"

    # DNS 解析后检查（防域名指向内网）
    try:
        for info in socket.getaddrinfo(hostname, None):
            resolved = info[4][0]
            if _is_private_ip(resolved):
                return False, f"主机 {hostname} 解析到私有地址 {resolved}"
    except socket.gaierror:
        return False, f"无法解析主机: {hostname}"

    return True, ""
