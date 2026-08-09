"""Noesis storage subsystem — DB engine, ORM, and Alembic migrations.

Lazy-loaded: importing ``noesis.storage`` does not pull SQLAlchemy or heavy
model modules. Use ``from noesis.storage.postgres.manager import pg_manager``
to access the shared engine/session.
"""
from __future__ import annotations

__all__: list[str] = []
