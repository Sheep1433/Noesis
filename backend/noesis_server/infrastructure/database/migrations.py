"""Re-export Alembic migration runner (transition shim).

Authoritative implementation in ``noesis.storage.postgres.manager.run_migrations``.
Removed in F4.
"""
from __future__ import annotations

from noesis.storage.postgres.manager import run_migrations

__all__ = ["run_migrations"]


if __name__ == "__main__":
    run_migrations()
