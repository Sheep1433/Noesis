"""记忆用户开关（单一开关；文件存储，零新表）。"""

from __future__ import annotations

import json
from pathlib import Path

from noesis.config.env import MemoryConfig
from noesis.config.user_data_paths import get_user_root


def _settings_path(user_id: str | int) -> Path:
    return get_user_root(user_id) / "memory_settings.json"


class MemoryUserSettings:
    @classmethod
    def is_enabled(cls, user_id: str | int) -> bool:
        path = _settings_path(user_id)
        if not path.is_file():
            return MemoryConfig.enabled_by_default
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return MemoryConfig.enabled_by_default
        return bool(data.get("enabled", MemoryConfig.enabled_by_default))

    @classmethod
    def set_enabled(cls, user_id: str | int, enabled: bool) -> bool:
        path = _settings_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"enabled": bool(enabled)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return cls.is_enabled(user_id)


__all__ = ["MemoryUserSettings"]
