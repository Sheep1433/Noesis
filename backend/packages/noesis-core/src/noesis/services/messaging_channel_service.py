"""通讯通道配置面（channels.json）；密钥脱敏；对接 Delivery Binding。"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from noesis.config.paths import DATA_DIR
from noesis.config.user_data_paths import ensure_user_channels_path, get_user_channels_path
from noesis.chat.delivery.channels import ChannelBinding, channel_bindings
from noesis.chat.delivery.channel_health import channel_health
from noesis.config.secrets import SecretCipher, SecretEncryptionUnavailable, secret_suffix
from noesis.errors.exceptions import ServiceException
from noesis.config.code_enum import IntentEnum

_ALLOWED_TYPES = frozenset({"telegram", "wechat", "feishu"})
_USERS_ROOT = DATA_DIR / "users"
_ALLOWED_QA_TYPES = {item.value[0] for item in IntentEnum}


@dataclass(frozen=True)
class RuntimeChannelConfig:
    """用户级运行时配置（Telegram 含 token；飞书只含用户配对与路由）。"""

    user_id: str
    channel_id: str
    channel_type: str
    bot_token: str
    pairing_chat_id: Optional[str]
    pairing_user_id: Optional[str]
    default_session_id: str
    default_qa_type: str
    display_name: str
    enabled: bool = True
    session_strategy: str = "persistent"
    delivery_preference: str = "reply"


def _encrypt_token(token: str) -> tuple[str, str | None]:
    try:
        return f"enc:{SecretCipher().encrypt(token)}", secret_suffix(token)
    except SecretEncryptionUnavailable as exc:
        raise ServiceException(data={"code": "secret_encryption_unavailable"}, message="敏感配置暂时无法保存") from exc


def _runtime_token(value: str | None) -> str:
    raw = str(value or "")
    if raw.startswith("enc:"):
        return SecretCipher().decrypt(raw.removeprefix("enc:"))
    return raw


def _mask_token(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    t = str(token)
    if len(t) <= 8:
        return "****"
    return f"****{t[-4:]}"


def _load_raw(user_id: str | int) -> Dict[str, Any]:
    path = get_user_channels_path(user_id)
    if not path.is_file():
        return {"channels": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"channels": []}
    if not isinstance(data, dict):
        return {"channels": []}
    ch = data.get("channels")
    if not isinstance(ch, list):
        data["channels"] = []
    return data


def _save_raw(user_id: str | int, data: Dict[str, Any]) -> None:
    path = ensure_user_channels_path(user_id)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _sync_bindings(user_id, data.get("channels") or [])


def _sync_bindings(user_id: str | int, channels: List[Dict[str, Any]]) -> None:
    """将已配对通道同步到运行时 ChannelBindingStore。"""
    uid = str(user_id)
    channel_bindings.clear_user(uid)
    for ch in channels:
        if not ch.get("enabled"):
            continue
        pairing = ch.get("pairing") or {}
        external_id = (
            pairing.get("user_id")
            if str(ch.get("type") or "telegram") == "feishu"
            else pairing.get("chat_id") or ch.get("pairing_chat_id")
        )
        if not external_id:
            continue
        session_id = (
            (ch.get("routing") or {}).get("default_session_id")
            or ch.get("default_session_id")
            or str(ch.get("channel_id") or "")
        )
        if not session_id or len(session_id) > 36:
            # t_chat_session.id 为 VARCHAR(36)；禁止 channel:{uuid} 等超长前缀
            continue
        channel_bindings.put(
            ChannelBinding(
                user_id=uid,
                channel_type=str(ch.get("type") or "telegram"),
                external_chat_id=str(external_id),
                session_id=str(session_id),
            )
        )


def _public_view(ch: Dict[str, Any], user_id: str | int) -> Dict[str, Any]:
    secrets = ch.get("secrets") if isinstance(ch.get("secrets"), dict) else {}
    pairing = ch.get("pairing") if isinstance(ch.get("pairing"), dict) else {}
    routing = ch.get("routing") if isinstance(ch.get("routing"), dict) else {}
    token = secrets.get("bot_token") or ch.get("bot_token")
    meta = ch.get("secret_meta") if isinstance(ch.get("secret_meta"), dict) else {}
    suffix = meta.get("bot_token_suffix")
    masked_token = f"****{suffix}" if suffix else None
    if masked_token is None and not str(token or "").startswith("enc:"):
        masked_token = _mask_token(token)
    return {
        "channel_id": ch.get("channel_id"),
        "type": ch.get("type"),
        "enabled": bool(ch.get("enabled")),
        "display_name": ch.get("display_name") or "",
        "bot_token_masked": masked_token,
        "has_token": bool(token),
        "pairing_chat_id": pairing.get("chat_id") or ch.get("pairing_chat_id"),
        "pairing_user_id": pairing.get("user_id") or ch.get("pairing_user_id"),
        "default_qa_type": routing.get("default_qa_type")
        or ch.get("default_qa_type")
        or "SUPER_AGENT_QA",
        "default_session_id": routing.get("default_session_id")
        or ch.get("default_session_id"),
        "session_strategy": routing.get("session_strategy") or "persistent",
        "delivery_preference": routing.get("delivery_preference") or "reply",
        "health": channel_health.get(user_id, str(ch.get("channel_id") or "")),
    }


def _runtime_config_from_channel(
    user_id: str | int,
    ch: Dict[str, Any],
) -> RuntimeChannelConfig | None:
    channel_id = str(ch.get("channel_id") or "")
    if not channel_id:
        return None
    channel_type = str(ch.get("type") or "telegram").lower()
    secrets = ch.get("secrets") if isinstance(ch.get("secrets"), dict) else {}
    pairing = ch.get("pairing") if isinstance(ch.get("pairing"), dict) else {}
    routing = ch.get("routing") if isinstance(ch.get("routing"), dict) else {}
    token = secrets.get("bot_token") or ch.get("bot_token")
    session_id = (
        routing.get("default_session_id")
        or ch.get("default_session_id")
        or channel_id
    )
    if len(str(session_id)) > 36:
        session_id = channel_id
    pairing_chat_id = pairing.get("chat_id") or ch.get("pairing_chat_id")
    pairing_user_id = pairing.get("user_id") or ch.get("pairing_user_id")
    return RuntimeChannelConfig(
        user_id=str(user_id),
        channel_id=channel_id,
        channel_type=channel_type,
        bot_token=_runtime_token(str(token or "")),
        pairing_chat_id=str(pairing_chat_id) if pairing_chat_id else None,
        pairing_user_id=str(pairing_user_id) if pairing_user_id else None,
        default_session_id=str(session_id),
        default_qa_type=str(
            routing.get("default_qa_type")
            or ch.get("default_qa_type")
            or "SUPER_AGENT_QA"
        ),
        display_name=str(ch.get("display_name") or channel_type),
        enabled=bool(ch.get("enabled")),
        session_strategy=str(routing.get("session_strategy") or "persistent"),
        delivery_preference=str(routing.get("delivery_preference") or "reply"),
    )


def _configured_user_ids() -> list[str]:
    if not _USERS_ROOT.is_dir():
        return []
    return [
        user_dir.name
        for user_dir in sorted(_USERS_ROOT.iterdir())
        if user_dir.is_dir() and (user_dir / "channels.json").is_file()
    ]


class MessagingChannelService:
    @staticmethod
    def _validate_routing(payload: Dict[str, Any]) -> None:
        qa_type = str(payload.get("default_qa_type") or "SUPER_AGENT_QA")
        if qa_type not in _ALLOWED_QA_TYPES:
            raise ValueError("不支持的默认 Agent 类型")
        if str(payload.get("session_strategy") or "persistent") not in {"persistent", "new_per_message"}:
            raise ValueError("不支持的会话策略")
        if str(payload.get("delivery_preference") or "reply") not in {"reply", "silent"}:
            raise ValueError("不支持的回复策略")

    @staticmethod
    def list_channels(user_id: str | int) -> List[Dict[str, Any]]:
        data = _load_raw(user_id)
        channels = data.get("channels") or []
        _sync_bindings(user_id, channels)
        return [_public_view(c, user_id) for c in channels if isinstance(c, dict)]

    @classmethod
    def create_channel(cls, user_id: str | int, payload: Dict[str, Any]) -> Dict[str, Any]:
        cls._validate_routing(payload)
        ctype = str(payload.get("type") or "telegram").strip().lower()
        if ctype not in _ALLOWED_TYPES:
            raise ValueError(f"不支持的通道类型: {ctype}")
        data = _load_raw(user_id)
        channels: List[Dict[str, Any]] = list(data.get("channels") or [])
        channel_id = str(uuid.uuid4())
        token = payload.get("bot_token")
        stored_token, suffix = _encrypt_token(str(token)) if token else (None, None)
        if ctype == "telegram" and not stored_token:
            raise ValueError("请填写 Bot Token")
        ch = {
            "channel_id": channel_id,
            "type": ctype,
            "enabled": bool(payload.get("enabled", True)),
            "display_name": str(payload.get("display_name") or ctype).strip(),
            "secrets": {
                **({"bot_token": stored_token} if stored_token else {}),
            },
            "secret_meta": {
                **({"bot_token_suffix": suffix} if suffix else {}),
            },
            "pairing": {
                **({"chat_id": payload.get("pairing_chat_id")} if payload.get("pairing_chat_id") else {}),
                **({"user_id": payload.get("pairing_user_id")} if payload.get("pairing_user_id") else {}),
            },
            "routing": {
                "default_qa_type": payload.get("default_qa_type") or "SUPER_AGENT_QA",
                "default_session_id": payload.get("default_session_id") or channel_id,
                "session_strategy": payload.get("session_strategy") or "persistent",
                "delivery_preference": payload.get("delivery_preference") or "reply",
            },
        }
        channels.append(ch)
        data["channels"] = channels
        _save_raw(user_id, data)
        return _public_view(ch, user_id)

    @classmethod
    def update_channel(
        cls, user_id: str | int, channel_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        cls._validate_routing(payload)
        data = _load_raw(user_id)
        channels: List[Dict[str, Any]] = list(data.get("channels") or [])
        idx = next((i for i, c in enumerate(channels) if c.get("channel_id") == channel_id), -1)
        if idx < 0:
            raise KeyError(channel_id)
        ch = dict(channels[idx])
        if "enabled" in payload:
            ch["enabled"] = bool(payload["enabled"])
        if payload.get("display_name") is not None:
            ch["display_name"] = str(payload.get("display_name") or "").strip()
        if payload.get("type"):
            ctype = str(payload["type"]).strip().lower()
            if ctype not in _ALLOWED_TYPES:
                raise ValueError(f"不支持的通道类型: {ctype}")
            ch["type"] = ctype
        secrets = dict(ch.get("secrets") or {})
        token_action = str(payload.get("bot_token_action") or ("replace" if payload.get("bot_token") else "keep"))
        secret_meta = dict(ch.get("secret_meta") or {})
        if token_action == "replace":
            token = str(payload.get("bot_token") or "").strip()
            if not token:
                raise ValueError("替换 Token 时必须填写新值")
            secrets["bot_token"], secret_meta["bot_token_suffix"] = _encrypt_token(token)
        elif token_action == "clear":
            secrets.pop("bot_token", None)
            secret_meta.pop("bot_token_suffix", None)
        elif token_action != "keep":
            raise ValueError("不支持的 Token 写入动作")
        ch["secrets"] = secrets
        ch["secret_meta"] = secret_meta
        pairing = dict(ch.get("pairing") or {})
        if "pairing_chat_id" in payload:
            if payload["pairing_chat_id"]:
                pairing["chat_id"] = payload["pairing_chat_id"]
            else:
                pairing.pop("chat_id", None)
        if "pairing_user_id" in payload:
            if payload["pairing_user_id"]:
                pairing["user_id"] = payload["pairing_user_id"]
            else:
                pairing.pop("user_id", None)
        ch["pairing"] = pairing
        routing = dict(ch.get("routing") or {})
        if payload.get("default_qa_type"):
            routing["default_qa_type"] = payload["default_qa_type"]
        if "default_session_id" in payload:
            routing["default_session_id"] = payload.get("default_session_id")
        if payload.get("session_strategy"):
            routing["session_strategy"] = payload["session_strategy"]
        if payload.get("delivery_preference"):
            routing["delivery_preference"] = payload["delivery_preference"]
        ch["routing"] = routing
        channels[idx] = ch
        data["channels"] = channels
        _save_raw(user_id, data)
        return _public_view(ch, user_id)

    @staticmethod
    def delete_channel(user_id: str | int, channel_id: str) -> None:
        data = _load_raw(user_id)
        channels = [c for c in (data.get("channels") or []) if c.get("channel_id") != channel_id]
        if len(channels) == len(data.get("channels") or []):
            raise KeyError(channel_id)
        data["channels"] = channels
        _save_raw(user_id, data)

    @staticmethod
    def channels_config_path(user_id: str | int) -> Path:
        return get_user_channels_path(user_id)

    @staticmethod
    def get_runtime_channel(user_id: str | int, channel_id: str) -> RuntimeChannelConfig:
        data = _load_raw(user_id)
        ch = next((item for item in data.get("channels", []) if isinstance(item, dict) and item.get("channel_id") == channel_id), None)
        if ch is None:
            raise KeyError(channel_id)
        config = _runtime_config_from_channel(user_id, ch)
        if config is None:
            raise KeyError(channel_id)
        return config

    @staticmethod
    def iter_enabled_runtime(
        channel_type: str = "telegram",
        *,
        user_id: Optional[str | int] = None,
    ) -> List[RuntimeChannelConfig]:
        """内部 API：返回含 bot_token 的启用通道；扫描 .noesis/users/*/channels.json。"""
        want = str(channel_type).lower()
        out: List[RuntimeChannelConfig] = []

        def _collect(uid: str | int) -> None:
            data = _load_raw(uid)
            channels = data.get("channels") or []
            _sync_bindings(uid, channels)
            for ch in channels:
                if not isinstance(ch, dict) or not ch.get("enabled"):
                    continue
                config = _runtime_config_from_channel(uid, ch)
                if config is None or config.channel_type != want:
                    continue
                if config.channel_type == "telegram" and not config.bot_token:
                    continue
                out.append(config)

        if user_id is not None:
            _collect(user_id)
            return out

        for configured_user_id in _configured_user_ids():
            _collect(configured_user_id)
        return out

    @staticmethod
    def sync_all_bindings() -> int:
        """启动时把磁盘配对刷进 ChannelBindingStore。"""
        count = 0
        for user_id in _configured_user_ids():
            data = _load_raw(user_id)
            channels = data.get("channels") or []
            _sync_bindings(user_id, channels)
            for ch in channels:
                if not isinstance(ch, dict) or not ch.get("enabled"):
                    continue
                config = _runtime_config_from_channel(user_id, ch)
                if config is None or config.channel_type not in _ALLOWED_TYPES:
                    continue
                if config.channel_type == "telegram" and not config.bot_token:
                    continue
                count += 1
        return count
