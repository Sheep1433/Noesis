"""敏感值静态加密与递归脱敏。"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

_SENSITIVE_KEY = re.compile(
    r"api.?key|token|secret|password|authorization|cookie|credential|private.?key",
    re.IGNORECASE,
)
REDACTED = "[REDACTED]"


class SecretEncryptionUnavailable(RuntimeError):
    """未配置加密密钥时拒绝敏感值写入。"""


class SecretDecryptionError(RuntimeError):
    """密文无法由当前密钥解密。"""


class SecretCipher:
    def __init__(self, key: str | bytes | None = None):
        raw_key = key or os.getenv("SETTINGS_ENCRYPTION_KEY", "")
        if not raw_key:
            raise SecretEncryptionUnavailable("用户级敏感设置加密未配置")
        try:
            self._fernet = Fernet(raw_key.encode() if isinstance(raw_key, str) else raw_key)
        except (TypeError, ValueError) as exc:
            raise SecretEncryptionUnavailable("用户级敏感设置加密密钥无效") from exc

    def encrypt(self, value: str) -> str:
        if not value:
            raise ValueError("敏感值不能为空")
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise SecretDecryptionError("敏感设置无法解密") from exc


def secret_suffix(value: str, length: int = 4) -> str | None:
    value = value.strip()
    return value[-length:] if value else None


def redact_sensitive(value: Any) -> Any:
    """生成可安全用于 API、日志、审计、诊断和导出的副本。"""
    if isinstance(value, Mapping):
        return {
            key: REDACTED if _SENSITIVE_KEY.search(str(key)) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_sensitive(item) for item in value]
    return value
