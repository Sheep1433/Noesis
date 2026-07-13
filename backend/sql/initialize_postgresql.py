#!/usr/bin/env python3
"""初始化 PostgreSQL 业务库并执行 Alembic 迁移。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from config.env import DataBaseConfig, get_config  # noqa: E402
from config.migrate import run_migrations  # noqa: E402


def server_url() -> str:
    return (
        f"postgresql+psycopg://{DataBaseConfig.postgres_user}:{quote_plus(DataBaseConfig.postgres_password)}@"
        f"{DataBaseConfig.postgres_host}:{DataBaseConfig.postgres_port}/postgres"
    )


def ensure_database(database: str, dry_run: bool) -> None:
    statement = f'CREATE DATABASE "{database}"'
    if dry_run:
        print(f"[dry-run] {statement}")
        return
    engine = create_engine(server_url(), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": database}).scalar()
            if not exists:
                conn.execute(text(statement))
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化 PostgreSQL（建库 + Alembic）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ensure_database(DataBaseConfig.postgres_database, args.dry_run)
    ensure_database(get_config.get_checkpoint_config().postgres_database, args.dry_run)
    if not args.dry_run:
        run_migrations()


if __name__ == "__main__":
    main()
