from sqlalchemy import text
from config.database import async_engine, AsyncSessionLocal, AsyncDatabaseInspector, inspector
from config.migrate import run_migrations
from common.logging import logger


async def get_db():
    """
    每一个请求处理完毕后会关闭当前连接，不同的请求使用不同的连接

    :return:
    """
    async with AsyncSessionLocal() as current_db:
        yield current_db


def get_inspector() -> AsyncDatabaseInspector:
    return inspector


async def init_database():
    """应用启动时执行 Alembic 迁移并校验数据库连接。"""
    logger.info('🔎 初始化数据库连接...')
    run_migrations()
    async with async_engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    logger.info('✅️ 数据库连接成功')
