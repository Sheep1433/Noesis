import os
import sys
import time

from loguru import logger

from noesis.config.paths import data_path


_VALID_LOG_LEVELS = {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}

# 请求级上下文默认值：非 HTTP 上下文（启动、后台任务、CLI）没有 request_id，占位符避免 KeyError
_DEFAULT_EXTRA = {"request_id": "-"}

_LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "{extra[request_id]} | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


def resolve_log_level(app_env: str | None = None) -> str:
    """Use INFO in prod unless an explicit valid override is supplied."""
    configured = os.getenv("NOESIS_LOG_LEVEL", "").strip().upper()
    if configured in _VALID_LOG_LEVELS:
        return configured
    environment = (app_env or os.getenv("APP_ENV", "dev")).strip().lower()
    return "INFO" if environment == "prod" else "DEBUG"


log_path = data_path("logs")
# 全量日志（dev 为 DEBUG、prod 为 INFO），按天一个文件；文件里并非只有 error
log_file = log_path / f"{time.strftime('%Y-%m-%d')}_noesis.log"
log_level = resolve_log_level()

logger.configure(extra=_DEFAULT_EXTRA)
logger.remove()
logger.add(sys.stderr, level=log_level, format=_LOG_FORMAT)
logger.add(
    str(log_file),
    level=log_level,
    format=_LOG_FORMAT,
    rotation="50MB",
    encoding="utf-8",
    enqueue=True,
    compression="zip",
)
