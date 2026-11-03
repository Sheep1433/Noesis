import os
import sys
import time
from pathlib import Path

from loguru import logger

from noesis.config.paths import data_path


_VALID_LOG_LEVELS = {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}


def _sanitize_segment(raw: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in raw)[:32]


def _log_file_stem() -> str:
    """日志文件名角色段：区分共享 ``logs/`` 目录的多个写者。

    三角色 / 多副本 bind 同一数据目录时（compose --scale），按「角色 + 实例」
    切分日志文件：多进程写同一文件时，进程内 50MB 轮转与压缩会互相覆盖
    （曾实际发生：同一天出现两个进程各自轮转出的 zip）。角色取入口脚本名
    （python web.py -> web，各形态启动方式均如此）；实例段用主机名——
    compose 副本主机名即容器 ID，天然唯一，本地为主机名。
    """
    role = Path(sys.argv[0]).stem if sys.argv and sys.argv[0] else ""
    role_seg = _sanitize_segment(role) or "main"
    instance_seg = _sanitize_segment(os.uname().nodename) or "i"
    return f"{role_seg}_{instance_seg}"

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
# 全量日志（dev 为 DEBUG、prod 为 INFO），按天 + 写者（角色/实例）一个文件
log_file = log_path / f"{time.strftime('%Y-%m-%d')}_{_log_file_stem()}_noesis.log"
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
