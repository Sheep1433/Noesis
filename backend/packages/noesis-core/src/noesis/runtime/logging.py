import os
import sys
import time

from loguru import logger

from noesis.config.paths import data_path


_VALID_LOG_LEVELS = {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}


def resolve_log_level(app_env: str | None = None) -> str:
    """Use INFO in prod unless an explicit valid override is supplied."""
    configured = os.getenv("NOESIS_LOG_LEVEL", "").strip().upper()
    if configured in _VALID_LOG_LEVELS:
        return configured
    environment = (app_env or os.getenv("APP_ENV", "dev")).strip().lower()
    return "INFO" if environment == "prod" else "DEBUG"


log_path = data_path("logs")
log_path_error = log_path / f"{time.strftime('%Y-%m-%d')}_error.log"
log_level = resolve_log_level()

logger.remove()
logger.add(sys.stderr, level=log_level)
logger.add(
    str(log_path_error),
    level=log_level,
    rotation="50MB",
    encoding="utf-8",
    enqueue=True,
    compression="zip",
)
