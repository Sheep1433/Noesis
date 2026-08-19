"""Framework-free authentication domain entities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class AuthUser:
    id: int | None
    username: str
    password_hash: str
    mobile: str | None = None
    registration_invite_digest: str | None = None
    registration_invite_updated_at: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class AuthSession:
    id: str
    user_id: int
    session_digest: str
    csrf_digest: str
    created_at: int
    last_seen_at: int
    idle_expires_at: int
    absolute_expires_at: int
    revoked_at: int | None = None
    device_name: str | None = None
    user_agent_digest: str | None = None
    last_ip: str | None = None
    prev_csrf_digest: str | None = None
