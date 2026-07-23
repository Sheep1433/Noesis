"""Alembic 运行环境（同步 psycopg，与 asyncpg 应用层分离）。"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from config.database import SYNC_SQLALCHEMY_DATABASE_URL, Base

# 注册 ORM，供 autogenerate 与 metadata 对齐
import models.chat_models  # noqa: F401
import models.db_models  # noqa: F401
import models.scheduled_task_models  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", SYNC_SQLALCHEMY_DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
