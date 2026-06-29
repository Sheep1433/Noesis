"""Alembic 数据库迁移（同步驱动，供启动与 CLI 共用）。"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from common.logging import logger
from config.database import SYNC_SQLALCHEMY_DATABASE_URL

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"
_INITIAL_REVISION = "202606290001"


def _alembic_config() -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", SYNC_SQLALCHEMY_DATABASE_URL)
    return cfg


def _bootstrap_legacy_schema_stamp(cfg: Config) -> None:
    """init_sql 等旧方式已建表、但未写入 Alembic revision 时，标记 head 避免重复 CREATE TABLE。"""
    engine = create_engine(SYNC_SQLALCHEMY_DATABASE_URL)
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        if "t_user" not in tables:
            return
        from alembic.runtime.migration import MigrationContext

        current = MigrationContext.configure(conn).get_current_revision()
    if current is not None:
        return
    logger.info(
        "检测到已有 schema（revision 为空），标记迁移 {} 为已应用",
        _INITIAL_REVISION,
    )
    command.stamp(cfg, "head")


def run_migrations() -> None:
    """执行待应用的 Alembic 迁移（幂等）。"""
    cfg = _alembic_config()
    logger.info("执行数据库迁移 alembic upgrade head ...")
    _bootstrap_legacy_schema_stamp(cfg)
    command.upgrade(cfg, "head")
    logger.info("数据库迁移完成")


if __name__ == "__main__":
    run_migrations()
