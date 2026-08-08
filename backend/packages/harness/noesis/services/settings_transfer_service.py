"""Versioned, secret-free settings export and optimistic two-phase import."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from copy import deepcopy

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.config.mcp_config import load_user_mcp_json
from noesis.storage.postgres.models.scheduled_task import TUserScheduledTask
from noesis.storage.postgres.models.settings import TUserNotificationPreference
from noesis.services.messaging_channel_service import MessagingChannelService
from noesis.services.mcp_service import McpService
from noesis.services.scheduled_task_service import ScheduledTaskService
from noesis.services.scheduled_task_service import validate_cron_expr
from noesis.services.notification_preference_service import EVENT_TYPES, SURFACES
from noesis.services.settings_service import SettingsService
from noesis.services.user_memory_service import UserMemoryService

SCHEMA_VERSION = 1
_SENSITIVE_KEYS = frozenset({"secret", "api_key", "token", "bot_token", "headers", "secret_ciphertext", "secret_suffix"})


def _fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _strip_sensitive(value):
    if isinstance(value, dict):
        return {key: _strip_sensitive(item) for key, item in value.items() if key.lower() not in _SENSITIVE_KEYS}
    if isinstance(value, list):
        return [_strip_sensitive(item) for item in value]
    return value


class SettingsTransferService:
    @classmethod
    async def export(cls, db: AsyncSession, user_id: int) -> dict:
        tasks = list((await db.execute(select(TUserScheduledTask).where(TUserScheduledTask.user_id == user_id, TUserScheduledTask.deleted_at.is_(None)))).scalars())
        preferences = list((await db.execute(select(TUserNotificationPreference).where(TUserNotificationPreference.user_id == user_id))).scalars())
        mcp = load_user_mcp_json(user_id).mcpServers
        domains = {
            "automation": [{key: getattr(row, key) for key in ("name", "cron_expr", "timezone", "enabled", "qa_type", "prompt", "session_binding", "delivery")} for row in tasks],
            "channels": [{key: item.get(key) for key in ("type", "enabled", "display_name", "pairing_chat_id", "default_qa_type", "default_session_id", "session_strategy", "delivery_preference")} for item in MessagingChannelService.list_channels(user_id)],
            "memory": {name: UserMemoryService.read_file(user_id, name)["content"] for name in ("USER.md", "AGENTS.md")},
            "notifications": [{"event_type": row.event_type, "delivery_surface": row.delivery_surface, "enabled": row.enabled} for row in preferences],
            "mcp": _strip_sensitive(mcp),
        }
        return {"schema_version": SCHEMA_VERSION, "generated_at": int(time.time() * 1000), "domains": domains}

    @classmethod
    async def preview(cls, db: AsyncSession, user_id: int, manifest: dict) -> dict:
        cls._validate(manifest)
        current = await cls.export(db, user_id)
        changes = []
        for domain, value in manifest["domains"].items():
            current_value = current["domains"].get(domain)
            changes.append({"domain": domain, "action": "unchanged" if value == current_value else "replace", "current_count": len(current_value) if hasattr(current_value, "__len__") else 0, "incoming_count": len(value) if hasattr(value, "__len__") else 0})
        return {"preview_id": _fingerprint({"current": current["domains"], "incoming": manifest["domains"]}), "schema_version": SCHEMA_VERSION, "changes": changes, "conflicts": [], "errors": cls._domain_errors(manifest["domains"])}

    @classmethod
    async def _apply_validated(cls, db: AsyncSession, user_id: int, manifest: dict, preview_id: str) -> dict:
        preview = await cls.preview(db, user_id, manifest)
        if preview["errors"]:
            raise ValueError("设置文件包含无效配置，请修正后重新预览")
        if preview["preview_id"] != preview_id:
            raise RuntimeError("设置已变化，请重新预览后再导入")
        applied: list[str] = []
        domains = manifest["domains"]
        if "automation" in domains:
            existing_tasks = {item["name"]: item for item in await ScheduledTaskService.list_tasks(db, user_id)}
            try:
                for item in domains["automation"]:
                    if item["name"] in existing_tasks:
                        await ScheduledTaskService.update_task(db, user_id, existing_tasks[item["name"]]["id"], item, commit=False)
                    else:
                        await ScheduledTaskService.create_task(db, user_id, item, commit=False)
                await db.commit()
            except Exception:
                await db.rollback()
                raise
            applied.append("automation")
        if "channels" in domains:
            channel_path = MessagingChannelService.channels_config_path(user_id)
            channel_backup = channel_path.read_text(encoding="utf-8") if channel_path.is_file() else None
            existing_channels = {item["display_name"]: item for item in MessagingChannelService.list_channels(user_id)}
            try:
                for item in domains["channels"]:
                    current = existing_channels.get(item.get("display_name"))
                    payload = {**item, "bot_token_action": "keep"}
                    if current:
                        MessagingChannelService.update_channel(user_id, current["channel_id"], payload)
                    else:
                        MessagingChannelService.create_channel(user_id, {**payload, "enabled": False})
            except Exception:
                if channel_backup is not None:
                    channel_path.write_text(channel_backup, encoding="utf-8")
                elif channel_path.is_file():
                    channel_path.unlink()
                MessagingChannelService.list_channels(user_id)
                raise
            applied.append("channels")
        if "memory" in domains:
            old = {name: UserMemoryService.read_file(user_id, name)["content"] for name in ("USER.md", "AGENTS.md")}
            try:
                for name in ("USER.md", "AGENTS.md"):
                    if name in domains["memory"]:
                        UserMemoryService.write_file(user_id, name, domains["memory"][name])
                applied.append("memory")
            except Exception:
                for name, content in old.items():
                    UserMemoryService.write_file(user_id, name, content)
                raise
        if "notifications" in domains:
            try:
                await db.execute(delete(TUserNotificationPreference).where(TUserNotificationPreference.user_id == user_id))
                now = int(time.time() * 1000)
                for item in domains["notifications"]:
                    db.add(TUserNotificationPreference(id=str(uuid.uuid4()), user_id=user_id, event_type=item["event_type"], delivery_surface=item["delivery_surface"], enabled=bool(item["enabled"]), version=1, created_at=now, updated_at=now))
                await db.commit()
            except Exception:
                await db.rollback()
                raise
            applied.append("notifications")
        if "mcp" in domains:
            from noesis.config.user_data_paths import get_user_mcp_path
            mcp_path = get_user_mcp_path(user_id)
            mcp_backup = mcp_path.read_text(encoding="utf-8") if mcp_path.is_file() else None
            current_mcp = deepcopy(load_user_mcp_json(user_id).mcpServers)
            for server_id, incoming in domains["mcp"].items():
                preserved_headers = current_mcp.get(server_id, {}).get("headers")
                current_mcp[server_id] = deepcopy(incoming)
                if preserved_headers:
                    current_mcp[server_id]["headers"] = preserved_headers
            try:
                McpService.save_user_config_file(user_id, json.dumps({"mcpServers": current_mcp}, ensure_ascii=False))
            except Exception:
                if mcp_backup is not None:
                    mcp_path.write_text(mcp_backup, encoding="utf-8")
                elif mcp_path.is_file():
                    mcp_path.unlink()
                raise
            applied.append("mcp")
        await SettingsService.append_audit(db, user_id=user_id, action="settings.import", setting_domain="settings", summary={"domains": applied})
        await db.commit()
        return {"applied": applied}

    @classmethod
    async def apply(cls, db: AsyncSession, user_id: int, manifest: dict, preview_id: str) -> dict:
        try:
            return await cls._apply_validated(db, user_id, manifest, preview_id)
        except Exception as exc:
            await db.rollback()
            await SettingsService.append_audit(db, user_id=user_id, action="settings.import.failed", setting_domain="settings", summary={"error_category": type(exc).__name__})
            await db.commit()
            raise

    @classmethod
    async def reset(cls, db: AsyncSession, user_id: int) -> dict:
        await db.execute(delete(TUserNotificationPreference).where(TUserNotificationPreference.user_id == user_id))
        await SettingsService.append_audit(db, user_id=user_id, action="settings.reset", setting_domain="settings", summary={"domains": ["notifications"]})
        await db.commit()
        return {"reset": ["notifications"]}

    @staticmethod
    def _validate(manifest: dict) -> None:
        if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION or not isinstance(manifest.get("domains"), dict):
            raise ValueError("不支持的设置文件版本")
        if deepcopy(manifest) != _strip_sensitive(manifest):
            raise ValueError("设置文件包含不允许导入的敏感字段")
        allowed = {"automation", "channels", "memory", "notifications", "mcp"}
        if not set(manifest["domains"]).issubset(allowed):
            raise ValueError("设置文件包含不支持的设置域")

    @staticmethod
    def _domain_errors(domains: dict) -> list[dict]:
        errors: list[dict] = []
        for index, item in enumerate(domains.get("automation", [])):
            try:
                validate_cron_expr(str(item.get("cron_expr") or ""), str(item.get("timezone") or "Asia/Shanghai"))
            except (ValueError, AttributeError) as exc:
                errors.append({"domain": "automation", "index": index, "message": str(exc)})
        for index, item in enumerate(domains.get("notifications", [])):
            if item.get("event_type") not in EVENT_TYPES or item.get("delivery_surface") not in SURFACES:
                errors.append({"domain": "notifications", "index": index, "message": "通知类型或接收方式无效"})
        for index, item in enumerate(domains.get("channels", [])):
            try:
                MessagingChannelService._validate_routing(item)
            except ValueError as exc:
                errors.append({"domain": "channels", "index": index, "message": str(exc)})
        return errors
