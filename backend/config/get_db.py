from sqlalchemy import text
from config.database import async_engine, AsyncSessionLocal, Base, AsyncDatabaseInspector, inspector
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


async def init_create_table():
    """
    应用启动时初始化数据库连接

    :return:
    """
    import models.chat_models  # noqa: F401
    import models.db_models  # noqa: F401

    logger.info('🔎 初始化数据库连接...')
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        logger.info('✅️ 数据库连接成功')
