import time

from loguru import logger

from common.paths import data_path

log_path = data_path("logs")
log_path_error = log_path / f"{time.strftime('%Y-%m-%d')}_error.log"

logger.add(
    str(log_path_error),
    rotation="50MB",
    encoding="utf-8",
    enqueue=True,
    compression="zip",
)
