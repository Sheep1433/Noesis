#!/usr/bin/env python3
"""
初始化 MySQL：创建库（若不存在）+ Alembic migrate。

用法（在 backend/ 目录）：
    uv run python sql/initialize_mysql.py
    uv run python sql/initialize_mysql.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from config.env import DataBaseConfig  # noqa: E402
from config.migrate import run_migrations  # noqa: E402


def _server_url() -> str:
    return (
        f"mysql+pymysql://{DataBaseConfig.mysql_user}:"
        f"{quote_plus(DataBaseConfig.mysql_password)}@"
        f"{DataBaseConfig.mysql_host}:{DataBaseConfig.mysql_port}/"
    )


def ensure_database(*, dry_run: bool) -> None:
    db_name = DataBaseConfig.mysql_database
    ddl = (
        f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
    if dry_run:
        print(f"[dry-run] {ddl}")
        return

    engine = create_engine(_server_url(), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text(ddl))
            conn.commit()
    finally:
        engine.dispose()
    print(f"数据库已就绪: {db_name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化 MySQL（建库 + Alembic）")
    parser.add_argument("--dry-run", action="store_true", help="仅打印建库语句，不执行迁移")
    args = parser.parse_args()

    ensure_database(dry_run=args.dry_run)
    if args.dry_run:
        print("[dry-run] 跳过 alembic upgrade head")
        return

    run_migrations()
    print("Alembic 迁移完成（含演示账号 admin / 123456，部署后请修改密码）")


if __name__ == "__main__":
    main()
