"""Application identifiers."""

from __future__ import annotations

import secrets
import time
import uuid


def new_uuid7() -> str:
    """Return a UUIDv7 string without requiring a third-party dependency.

    Python 3.11 does not expose ``uuid.uuid7`` yet.  UUIDv7 keeps the
    timestamp in the most significant bits, which gives PostgreSQL indexes
    better locality than UUIDv4 while remaining safe to expose externally.
    """

    timestamp_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    random_a = secrets.randbits(12)
    random_b = secrets.randbits(62)
    value = (
        (timestamp_ms << 80)
        | (0x7 << 76)
        | (random_a << 64)
        | (0b10 << 62)
        | random_b
    )
    return str(uuid.UUID(int=value))
