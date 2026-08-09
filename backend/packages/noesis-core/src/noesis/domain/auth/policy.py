"""Pure authentication rules and token helpers."""

from __future__ import annotations

import hashlib
import hmac

from noesis.domain.auth.entities import AuthSession


def digest_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def session_expiry(now_ms: int, *, idle_days: int, absolute_days: int) -> tuple[int, int]:
    return (
        now_ms + idle_days * 86_400_000,
        now_ms + absolute_days * 86_400_000,
    )


def is_session_valid(session: AuthSession, now_ms: int) -> bool:
    return (
        session.revoked_at is None
        and session.idle_expires_at > now_ms
        and session.absolute_expires_at > now_ms
    )


def touch_session(
    session: AuthSession,
    *,
    now_ms: int,
    renewal_window_minutes: int,
    idle_days: int,
) -> bool:
    if now_ms - session.last_seen_at < renewal_window_minutes * 60_000:
        return False
    idle_expiry = now_ms + idle_days * 86_400_000
    session.last_seen_at = now_ms
    session.idle_expires_at = min(idle_expiry, session.absolute_expires_at)
    return True


def remaining_seconds(session: AuthSession, now_ms: int) -> int:
    return max(0, (min(session.idle_expires_at, session.absolute_expires_at) - now_ms) // 1000)


def verify_csrf(session: AuthSession, token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(session.csrf_digest, digest_secret(token))


def verify_invite_digest(stored_digest: str | None, code: str) -> bool:
    return bool(stored_digest) and hmac.compare_digest(stored_digest, digest_secret(code))
