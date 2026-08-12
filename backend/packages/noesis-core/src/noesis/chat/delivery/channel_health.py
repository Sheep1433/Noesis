"""Delivery 权威的通道运行状态与最近活动。"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from threading import RLock


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class ChannelRuntimeHealth:
    user_id: str
    channel_id: str
    status: str = "unknown"
    checked_at: int = 0
    last_inbound_at: int | None = None
    last_inbound_status: str | None = None
    last_outbound_at: int | None = None
    last_outbound_status: str | None = None
    error_category: str | None = None
    message: str = "尚未检查"
    correlation_id: str | None = None


class ChannelHealthStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], ChannelRuntimeHealth] = {}
        self._lock = RLock()

    def _item(self, user_id: str | int, channel_id: str) -> ChannelRuntimeHealth:
        key = (str(user_id), str(channel_id))
        with self._lock:
            return self._items.setdefault(key, ChannelRuntimeHealth(user_id=key[0], channel_id=key[1]))

    def report_status(self, user_id: str | int, channel_id: str, status: str, message: str, *, error_category: str | None = None, correlation_id: str | None = None) -> None:
        item = self._item(user_id, channel_id)
        with self._lock:
            item.status, item.message, item.checked_at = status, message, _now_ms()
            item.error_category, item.correlation_id = error_category, correlation_id

    def report_activity(self, user_id: str | int, channel_id: str, direction: str, status: str) -> None:
        item = self._item(user_id, channel_id)
        now = _now_ms()
        with self._lock:
            if direction == "inbound":
                item.last_inbound_at, item.last_inbound_status = now, status
            else:
                item.last_outbound_at, item.last_outbound_status = now, status

    def get(self, user_id: str | int, channel_id: str) -> dict:
        item = self._item(user_id, channel_id)
        with self._lock:
            safe = asdict(item)
        safe.pop("user_id", None)
        return safe

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


channel_health = ChannelHealthStore()
