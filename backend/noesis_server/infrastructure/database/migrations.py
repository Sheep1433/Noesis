"""平台 Alembic 迁移入口（委托 noesis.storage.postgres.manager）。"""
from __future__ import annotations

from noesis.storage.postgres.manager import run_migrations

__all__ = ["run_migrations"]


if __name__ == "__main__":
    run_migrations()
