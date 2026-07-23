"""通讯通道配置面（channels.json）；密钥脱敏；对接 Delivery Binding。"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from common.paths import DATA_DIR
from config.user_data_paths import ensure_user_channels_path, get_user_channels_path
from domain.chat.delivery.channels import ChannelBinding, channel_bindings

_ALLOWED_TYPES = frozenset({"telegram", "wechat", "feishu"})
_USERS_ROOT = DATA_DIR / "users"


@dataclass(frozen=True)
class RuntimeChannelConfig:
    """内部运行时配置（含 bot_token）；禁止经 HTTP 回传。"""

    user_id: str
    channel_id: str
    channel_type: str
    bot_token: str
    pairing_chat_id: Optional[str]
    default_session_id: str
    default_qa_type: str
    display_name: str
    enabled: bool = True


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
        chat_id = (ch.get("pairing") or {}).get("chat_id") or ch.get("pairing_chat_id")
        if not chat_id:
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
                external_chat_id=str(chat_id),
                session_id=str(session_id),
            )
        )


def _public_view(ch: Dict[str, Any]) -> Dict[str, Any]:
    secrets = ch.get("secrets") if isinstance(ch.get("secrets"), dict) else {}
    pairing = ch.get("pairing") if isinstance(ch.get("pairing"), dict) else {}
    routing = ch.get("routing") if isinstance(ch.get("routing"), dict) else {}
    token = secrets.get("bot_token") or ch.get("bot_token")
    return {
        "channel_id": ch.get("channel_id"),
        "type": ch.get("type"),
        "enabled": bool(ch.get("enabled")),
        "display_name": ch.get("display_name") or "",
        "bot_token_masked": _mask_token(token),
        "has_token": bool(token),
        "pairing_chat_id": pairing.get("chat_id") or ch.get("pairing_chat_id"),
        "default_qa_type": routing.get("default_qa_type")
        or ch.get("default_qa_type")
        or "SUPER_AGENT_QA",
        "default_session_id": routing.get("default_session_id")
        or ch.get("default_session_id"),
    }


class MessagingChannelService:
    @staticmethod
    def list_channels(user_id: str | int) -> List[Dict[str, Any]]:
        data = _load_raw(user_id)
        channels = data.get("channels") or []
        _sync_bindings(user_id, channels)
        return [_public_view(c) for c in channels if isinstance(c, dict)]

    @classmethod
    def create_channel(cls, user_id: str | int, payload: Dict[str, Any]) -> Dict[str, Any]:
        ctype = str(payload.get("type") or "telegram").strip().lower()
        if ctype not in _ALLOWED_TYPES:
            raise ValueError(f"不支持的通道类型: {ctype}")
        data = _load_raw(user_id)
        channels: List[Dict[str, Any]] = list(data.get("channels") or [])
        channel_id = str(uuid.uuid4())
        token = payload.get("bot_token")
        ch = {
            "channel_id": channel_id,
            "type": ctype,
            "enabled": bool(payload.get("enabled", True)),
            "display_name": str(payload.get("display_name") or ctype).strip(),
            "secrets": {"bot_token": token} if token else {},
            "pairing": {"chat_id": payload.get("pairing_chat_id")}
            if payload.get("pairing_chat_id")
            else {},
            "routing": {
                "default_qa_type": payload.get("default_qa_type") or "SUPER_AGENT_QA",
                "default_session_id": payload.get("default_session_id") or channel_id,
            },
        }
        channels.append(ch)
        data["channels"] = channels
        _save_raw(user_id, data)
        return _public_view(ch)

    @classmethod
    def update_channel(
        cls, user_id: str | int, channel_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
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
        if payload.get("bot_token"):
            secrets["bot_token"] = payload["bot_token"]
        ch["secrets"] = secrets
        pairing = dict(ch.get("pairing") or {})
        if "pairing_chat_id" in payload:
            if payload["pairing_chat_id"]:
                pairing["chat_id"] = payload["pairing_chat_id"]
            else:
                pairing.pop("chat_id", None)
        ch["pairing"] = pairing
        routing = dict(ch.get("routing") or {})
        if payload.get("default_qa_type"):
            routing["default_qa_type"] = payload["default_qa_type"]
        if "default_session_id" in payload:
            routing["default_session_id"] = payload.get("default_session_id")
        ch["routing"] = routing
        channels[idx] = ch
        data["channels"] = channels
        _save_raw(user_id, data)
        return _public_view(ch)

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
    def iter_enabled_runtime(
        channel_type: str = "telegram",
        *,
        user_id: Optional[str | int] = None,
    ) -> List[RuntimeChannelConfig]:
        """内部 API：返回含 bot_token 的启用通道；扫描 .data/users/*/channels.json。"""
        want = str(channel_type).lower()
        out: List[RuntimeChannelConfig] = []

        def _collect(uid: str | int) -> None:
            data = _load_raw(uid)
            channels = data.get("channels") or []
            _sync_bindings(uid, channels)
            for ch in channels:
                if not isinstance(ch, dict) or not ch.get("enabled"):
                    continue
                secrets = ch.get("secrets") if isinstance(ch.get("secrets"), dict) else {}
                token = secrets.get("bot_token") or ch.get("bot_token")
                if not token:
                    continue
                ctype = str(ch.get("type") or "telegram").lower()
                if ctype != want:
                    continue
                channel_id = str(ch.get("channel_id") or "")
                if not channel_id:
                    continue
                pairing = ch.get("pairing") if isinstance(ch.get("pairing"), dict) else {}
                routing = ch.get("routing") if isinstance(ch.get("routing"), dict) else {}
                session_id = (
                    routing.get("default_session_id")
                    or ch.get("default_session_id")
                    or channel_id
                )
                if len(str(session_id)) > 36:
                    session_id = channel_id
                pair_id = pairing.get("chat_id") or ch.get("pairing_chat_id")
                out.append(
                    RuntimeChannelConfig(
                        user_id=str(uid),
                        channel_id=channel_id,
                        channel_type=ctype,
                        bot_token=str(token),
                        pairing_chat_id=str(pair_id) if pair_id else None,
                        default_session_id=str(session_id),
                        default_qa_type=str(
                            routing.get("default_qa_type")
                            or ch.get("default_qa_type")
                            or "SUPER_AGENT_QA"
                        ),
                        display_name=str(ch.get("display_name") or ctype),
                        enabled=True,
                    )
                )

        if user_id is not None:
            _collect(user_id)
            return out

        if not _USERS_ROOT.is_dir():
            return out
        for user_dir in sorted(_USERS_ROOT.iterdir()):
            if not user_dir.is_dir():
                continue
            if not (user_dir / "channels.json").is_file():
                continue
            _collect(user_dir.name)
        return out

    @staticmethod
    def sync_all_bindings() -> int:
        """启动时把磁盘配对刷进 ChannelBindingStore。"""
        n = 0
        for _ in MessagingChannelService.iter_enabled_runtime("telegram"):
            n += 1
        for _ in MessagingChannelService.iter_enabled_runtime("wechat"):
            n += 1
        return n
