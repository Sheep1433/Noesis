"""配置入口：敏感项来自 .env，运行参数来自 config.yaml。"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from config.yaml_config import AppYamlConfig, load_app_yaml


def _legacy_env(name: str, default: str) -> str:
    """迁移期：config.yaml 未覆盖时仍可读旧 .env 变量。"""
    return os.getenv(name, default)


def _legacy_env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes")


def _legacy_env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _legacy_env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# .env：仅敏感 / 环境相关
# ---------------------------------------------------------------------------


class EnvSecrets(BaseSettings):
    """仅通过环境变量加载的敏感配置。"""

    model_config = SettingsConfigDict(extra="ignore")

    app_env: str = Field(default="dev", alias="APP_ENV")

    jwt_secret_key: str = Field(
        default="b01c66dc2c58dc6a0aabfe2144256be36226de378bf87f72c0c795dda67f4d55",
        alias="JWT_SECRET_KEY",
    )

    mysql_password: str = Field(default="mysqlroot", alias="MYSQL_PASSWORD")

    model_api_key: str = Field(default="", alias="MODEL_API_KEY")
    embedding_model_api_key: str = Field(default="", alias="EMBEDDING_MODEL_API_KEY")
    summarization_model_api_key: str = Field(default="", alias="SUMMARIZATION_MODEL_API_KEY")

    qdrant_api_key: str = Field(default="", alias="QDRANT_API_KEY")

    langfuse_secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")
    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")

    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")


# ---------------------------------------------------------------------------
# 合并后的运行时视图（保持原有 import 名称）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppSettings:
    app_env: str
    app_name: str
    app_root_path: str
    app_host: str
    app_port: int
    app_version: str
    app_reload: bool


@dataclass(frozen=True)
class JwtSettings:
    jwt_secret_key: str
    jwt_algorithm: str
    jwt_expire_minutes: int
    jwt_redis_expire_minutes: int
    jwt_stop_token_expire_minutes: int


@dataclass(frozen=True)
class DataBaseSettings:
    mysql_host: str
    mysql_port: int
    mysql_user: str
    mysql_password: str
    mysql_database: str
    db_echo: bool
    db_max_overflow: int
    db_pool_size: int
    db_pool_recycle: int
    db_pool_timeout: int


@dataclass(frozen=True)
class ModelSettings:
    model_type: str
    model_name: str
    model_temperature: float
    model_base_url: str
    model_api_key: str
    embedding_model_api_key: str
    embedding_model_name: str
    rerank_model_name: str
    show_thinking_process: str
    request_timeout: float
    max_retries: int
    max_tokens: int
    top_p: float
    frequency_penalty: float
    presence_penalty: float
    streaming: bool
    context_max_input_tokens: int
    context_display_enabled: bool
    summarization_enabled: bool
    summarization_model_type: str
    summarization_model_name: str
    summarization_model_base_url: str
    summarization_model_api_key: str
    summarization_model_temperature: float
    summarization_max_tokens_before_summary: int
    summarization_trigger_tokens: int
    summarization_trigger_fraction: float
    summarization_max_input_tokens: int
    summarization_tool_offload_threshold: int
    summarization_max_retention_ratio: float
    summarization_messages_to_keep: int
    loop_detection_enabled: bool
    loop_detection_warn_threshold: int
    loop_detection_hard_limit: int
    loop_detection_window_size: int
    loop_detection_max_tracked_threads: int
    loop_detection_tool_freq_warn: int
    loop_detection_tool_freq_hard_limit: int
    dangling_tool_call_repair_enabled: bool
    tool_call_limit_enabled: bool
    tool_call_limit_thread_limit: int | None
    tool_call_limit_run_limit: int | None
    tool_call_limit_exit_behavior: str
    tool_call_limit_task_run_limit: int | None


@dataclass(frozen=True)
class StreamSettings:
    sse_keepalive_interval_seconds: float


@dataclass(frozen=True)
class OtherSettings:
    skills_filesystem_root: str


@dataclass(frozen=True)
class QdrantSettings:
    qdrant_host: str
    qdrant_port: int
    qdrant_api_key: str
    qdrant_timeout: int
    qdrant_grpc_port: int
    qdrant_prefer_grpc: bool
    qdrant_default_collection: str
    requirement_docs_collection: str
    test_case_docs_collection: str
    test_case_upload_collection: str
    case_rag_historical_requirements_enabled: bool


@dataclass(frozen=True)
class LangfuseSettings:
    langfuse_tracing_enabled: bool
    langfuse_secret_key: str
    langfuse_public_key: str
    langfuse_base_url: str


@dataclass(frozen=True)
class WebToolsSettings:
    max_search_results: int
    fetch_max_chars: int
    fetch_timeout_seconds: int
    tavily_api_key: str


@dataclass(frozen=True)
class CheckpointSettings:
    db_path: str


@dataclass(frozen=True)
class ChatAttachmentSettings:
    enabled: bool
    dir: str
    ttl_days: int
    max_file_mb: int
    max_count_per_session: int
    auto_convert: bool
    max_image_mb: int
    vision_enabled: bool
    reinject_session_images: bool
    max_images_per_message: int
    tiny_inline_chars: int
    read_page_lines: int
    preview_chars: int


def _legacy_env(key: str, default: str) -> str:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip()


def _legacy_env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return default
    return int(raw)


def _legacy_env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return default
    return float(raw)


def _legacy_env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("true", "1", "yes", "on")


def _build_app(secrets: EnvSecrets, yaml_cfg: AppYamlConfig) -> AppSettings:
    app = yaml_cfg.app
    return AppSettings(
        app_env=secrets.app_env,
        app_name=_legacy_env("APP_NAME", app.name),
        app_root_path=_legacy_env("APP_ROOT_PATH", app.root_path),
        app_host=_legacy_env("APP_HOST", app.host),
        app_port=_legacy_env_int("APP_PORT", app.port),
        app_version=_legacy_env("APP_VERSION", app.version),
        app_reload=_legacy_env_bool("APP_RELOAD", app.reload),
    )


def _build_jwt(secrets: EnvSecrets, yaml_cfg: AppYamlConfig) -> JwtSettings:
    jwt = yaml_cfg.jwt
    return JwtSettings(
        jwt_secret_key=secrets.jwt_secret_key,
        jwt_algorithm=_legacy_env("JWT_ALGORITHM", jwt.algorithm),
        jwt_expire_minutes=_legacy_env_int("JWT_EXPIRE_MINUTES", jwt.expire_minutes),
        jwt_redis_expire_minutes=_legacy_env_int(
            "JWT_REDIS_EXPIRE_MINUTES", jwt.redis_expire_minutes
        ),
        jwt_stop_token_expire_minutes=_legacy_env_int(
            "JWT_STOP_TOKEN_EXPIRE_MINUTES", jwt.stop_token_expire_minutes
        ),
    )


def _build_database(secrets: EnvSecrets, yaml_cfg: AppYamlConfig) -> DataBaseSettings:
    db = yaml_cfg.database
    return DataBaseSettings(
        mysql_host=_legacy_env("MYSQL_HOST", db.host),
        mysql_port=_legacy_env_int("MYSQL_PORT", db.port),
        mysql_user=_legacy_env("MYSQL_USER", db.user),
        mysql_password=secrets.mysql_password,
        mysql_database=_legacy_env("MYSQL_DATABASE", db.database),
        db_echo=_legacy_env_bool("DB_ECHO", db.echo),
        db_max_overflow=_legacy_env_int("DB_MAX_OVERFLOW", db.max_overflow),
        db_pool_size=_legacy_env_int("DB_POOL_SIZE", db.pool_size),
        db_pool_recycle=_legacy_env_int("DB_POOL_RECYCLE", db.pool_recycle),
        db_pool_timeout=_legacy_env_int("DB_POOL_TIMEOUT", db.pool_timeout),
    )


def _build_model(secrets: EnvSecrets, yaml_cfg: AppYamlConfig) -> ModelSettings:
    m = yaml_cfg.model
    gen = m.generation
    ctx = yaml_cfg.context
    s = yaml_cfg.summarization
    sm = s.model
    loop = yaml_cfg.loop_detection
    runtime = yaml_cfg.agent_runtime
    return ModelSettings(
        model_type=_legacy_env("MODEL_TYPE", m.type),
        model_name=_legacy_env("MODEL_NAME", m.name),
        model_temperature=_legacy_env_float("MODEL_TEMPERATURE", m.temperature),
        model_base_url=_legacy_env("MODEL_BASE_URL", m.base_url),
        model_api_key=secrets.model_api_key,
        embedding_model_api_key=secrets.embedding_model_api_key,
        embedding_model_name=_legacy_env("EMBEDDING_MODEL_NAME", m.embedding_model_name),
        rerank_model_name=_legacy_env("RERANK_MODEL_NAME", m.rerank_model_name),
        show_thinking_process=_legacy_env(
            "SHOW_THINKING_PROCESS", "true" if m.show_thinking_process else "false"
        ),
        request_timeout=_legacy_env_float("REQUEST_TIMEOUT", m.request_timeout),
        max_retries=_legacy_env_int("MAX_RETRIES", m.max_retries),
        max_tokens=_legacy_env_int("MAX_TOKENS", gen.max_tokens),
        top_p=_legacy_env_float("TOP_P", gen.top_p),
        frequency_penalty=_legacy_env_float("FREQUENCY_PENALTY", gen.frequency_penalty),
        presence_penalty=_legacy_env_float("PRESENCE_PENALTY", gen.presence_penalty),
        streaming=_legacy_env_bool("STREAMING", gen.streaming),
        context_max_input_tokens=_legacy_env_int("CONTEXT_MAX_INPUT_TOKENS", ctx.max_input_tokens),
        context_display_enabled=_legacy_env_bool("CONTEXT_DISPLAY_ENABLED", ctx.display_enabled),
        summarization_enabled=_legacy_env_bool("SUMMARIZATION_ENABLED", s.enabled),
        summarization_model_type=_legacy_env("SUMMARIZATION_MODEL_TYPE", sm.type),
        summarization_model_name=_legacy_env("SUMMARIZATION_MODEL_NAME", sm.name),
        summarization_model_base_url=_legacy_env("SUMMARIZATION_MODEL_BASE_URL", sm.base_url),
        summarization_model_api_key=secrets.summarization_model_api_key,
        summarization_model_temperature=_legacy_env_float(
            "SUMMARIZATION_MODEL_TEMPERATURE", sm.temperature
        ),
        summarization_max_tokens_before_summary=_legacy_env_int(
            "SUMMARIZATION_MAX_TOKENS_BEFORE_SUMMARY", s.max_tokens_before_summary
        ),
        summarization_trigger_tokens=_legacy_env_int(
            "SUMMARIZATION_TRIGGER_TOKENS", s.trigger_tokens
        ),
        summarization_trigger_fraction=_legacy_env_float(
            "SUMMARIZATION_TRIGGER_FRACTION", s.trigger_fraction
        ),
        summarization_max_input_tokens=_legacy_env_int(
            "SUMMARIZATION_MAX_INPUT_TOKENS", s.max_input_tokens
        ),
        summarization_tool_offload_threshold=_legacy_env_int(
            "SUMMARIZATION_TOOL_OFFLOAD_THRESHOLD", s.tool_offload_threshold
        ),
        summarization_max_retention_ratio=_legacy_env_float(
            "SUMMARIZATION_MAX_RETENTION_RATIO", s.max_retention_ratio
        ),
        summarization_messages_to_keep=_legacy_env_int(
            "SUMMARIZATION_MESSAGES_TO_KEEP", s.messages_to_keep
        ),
        loop_detection_enabled=_legacy_env_bool("LOOP_DETECTION_ENABLED", loop.enabled),
        loop_detection_warn_threshold=_legacy_env_int(
            "LOOP_DETECTION_WARN_THRESHOLD", loop.warn_threshold
        ),
        loop_detection_hard_limit=_legacy_env_int("LOOP_DETECTION_HARD_LIMIT", loop.hard_limit),
        loop_detection_window_size=_legacy_env_int("LOOP_DETECTION_WINDOW_SIZE", loop.window_size),
        loop_detection_max_tracked_threads=_legacy_env_int(
            "LOOP_DETECTION_MAX_TRACKED_THREADS", loop.max_tracked_threads
        ),
        loop_detection_tool_freq_warn=_legacy_env_int(
            "LOOP_DETECTION_TOOL_FREQ_WARN", loop.tool_freq_warn
        ),
        loop_detection_tool_freq_hard_limit=_legacy_env_int(
            "LOOP_DETECTION_TOOL_FREQ_HARD_LIMIT", loop.tool_freq_hard_limit
        ),
        dangling_tool_call_repair_enabled=_legacy_env_bool(
            "DANGLING_TOOL_CALL_REPAIR_ENABLED", runtime.dangling_tool_call_repair_enabled
        ),
        tool_call_limit_enabled=_legacy_env_bool(
            "TOOL_CALL_LIMIT_ENABLED", runtime.tool_call_limit.enabled
        ),
        tool_call_limit_thread_limit=_legacy_env_int(
            "TOOL_CALL_LIMIT_THREAD_LIMIT", runtime.tool_call_limit.thread_limit or 0
        )
        or None,
        tool_call_limit_run_limit=(
            _legacy_env_int("TOOL_CALL_LIMIT_RUN_LIMIT", runtime.tool_call_limit.run_limit or 0)
            or None
        ),
        tool_call_limit_exit_behavior=_legacy_env(
            "TOOL_CALL_LIMIT_EXIT_BEHAVIOR", runtime.tool_call_limit.exit_behavior
        ),
        tool_call_limit_task_run_limit=(
            _legacy_env_int(
                "TOOL_CALL_LIMIT_TASK_RUN_LIMIT",
                runtime.tool_call_limit.task_run_limit or 0,
            )
            or None
        ),
    )


def _build_stream(yaml_cfg: AppYamlConfig) -> StreamSettings:
    stream = yaml_cfg.stream
    return StreamSettings(
        sse_keepalive_interval_seconds=_legacy_env_float(
            "SSE_KEEPALIVE_INTERVAL_SECONDS", stream.sse_keepalive_interval_seconds
        ),
    )


def _build_other(yaml_cfg: AppYamlConfig) -> OtherSettings:
    other = yaml_cfg.other
    return OtherSettings(
        skills_filesystem_root=_legacy_env("SKILLS_FILESYSTEM_ROOT", other.skills_filesystem_root),
    )


def _build_qdrant(secrets: EnvSecrets, yaml_cfg: AppYamlConfig) -> QdrantSettings:
    q = yaml_cfg.qdrant
    return QdrantSettings(
        qdrant_host=_legacy_env("QDRANT_HOST", q.host),
        qdrant_port=_legacy_env_int("QDRANT_PORT", q.port),
        qdrant_api_key=secrets.qdrant_api_key,
        qdrant_timeout=_legacy_env_int("QDRANT_TIMEOUT", q.timeout),
        qdrant_grpc_port=_legacy_env_int("QDRANT_GRPC_PORT", q.grpc_port),
        qdrant_prefer_grpc=_legacy_env_bool("QDRANT_PREFER_GRPC", q.prefer_grpc),
        qdrant_default_collection=_legacy_env("QDRANT_DEFAULT_COLLECTION", q.default_collection),
        requirement_docs_collection=_legacy_env(
            "REQUIREMENT_DOCS_COLLECTION", q.requirement_docs_collection
        ),
        test_case_docs_collection=_legacy_env(
            "TEST_CASE_DOCS_COLLECTION", q.test_case_docs_collection
        ),
        test_case_upload_collection=_legacy_env(
            "TEST_CASE_UPLOAD_COLLECTION", q.test_case_upload_collection
        ),
        case_rag_historical_requirements_enabled=_legacy_env_bool(
            "CASE_RAG_HISTORICAL_REQUIREMENTS_ENABLED",
            q.case_rag_historical_requirements_enabled,
        ),
    )


def _build_langfuse(secrets: EnvSecrets, yaml_cfg: AppYamlConfig) -> LangfuseSettings:
    lf = yaml_cfg.langfuse
    return LangfuseSettings(
        langfuse_tracing_enabled=_legacy_env_bool("LANGFUSE_TRACING_ENABLED", lf.tracing_enabled),
        langfuse_secret_key=secrets.langfuse_secret_key,
        langfuse_public_key=secrets.langfuse_public_key,
        langfuse_base_url=_legacy_env("LANGFUSE_BASE_URL", lf.base_url),
    )


def _build_web_tools(secrets: EnvSecrets, yaml_cfg: AppYamlConfig) -> WebToolsSettings:
    wt = yaml_cfg.web_tools
    return WebToolsSettings(
        max_search_results=_legacy_env_int("WEB_MAX_SEARCH_RESULTS", wt.max_search_results),
        fetch_max_chars=_legacy_env_int("WEB_FETCH_MAX_CHARS", wt.fetch_max_chars),
        fetch_timeout_seconds=_legacy_env_int("WEB_FETCH_TIMEOUT_SECONDS", wt.fetch_timeout_seconds),
        tavily_api_key=secrets.tavily_api_key,
    )


def _build_checkpoint(yaml_cfg: AppYamlConfig) -> CheckpointSettings:
    cp = yaml_cfg.checkpoint
    return CheckpointSettings(
        db_path=_legacy_env("LANGGRAPH_CHECKPOINT_DB_PATH", cp.db_path),
    )


def _build_chat_attachment(yaml_cfg: AppYamlConfig) -> ChatAttachmentSettings:
    ca = yaml_cfg.chat_attachment
    return ChatAttachmentSettings(
        enabled=_legacy_env_bool("CHAT_ATTACHMENT_ENABLED", ca.enabled),
        dir=_legacy_env("CHAT_ATTACHMENT_DIR", ca.dir),
        ttl_days=_legacy_env_int("CHAT_ATTACHMENT_TTL_DAYS", ca.ttl_days),
        max_file_mb=_legacy_env_int("CHAT_ATTACHMENT_MAX_FILE_MB", ca.max_file_mb),
        max_count_per_session=_legacy_env_int(
            "CHAT_ATTACHMENT_MAX_COUNT_PER_SESSION", ca.max_count_per_session
        ),
        auto_convert=_legacy_env_bool("CHAT_ATTACHMENT_AUTO_CONVERT", ca.auto_convert),
        max_image_mb=_legacy_env_int("CHAT_ATTACHMENT_MAX_IMAGE_MB", ca.max_image_mb),
        vision_enabled=_legacy_env_bool("CHAT_ATTACHMENT_VISION_ENABLED", ca.vision_enabled),
        reinject_session_images=_legacy_env_bool(
            "CHAT_ATTACHMENT_REINJECT_SESSION_IMAGES", ca.reinject_session_images
        ),
        max_images_per_message=_legacy_env_int(
            "CHAT_ATTACHMENT_MAX_IMAGES_PER_MESSAGE", ca.max_images_per_message
        ),
        tiny_inline_chars=_legacy_env_int(
            "CHAT_ATTACHMENT_TINY_INLINE_CHARS", ca.tiny_inline_chars
        ),
        read_page_lines=_legacy_env_int(
            "CHAT_ATTACHMENT_READ_PAGE_LINES", ca.read_page_lines
        ),
        preview_chars=_legacy_env_int("CHAT_ATTACHMENT_PREVIEW_CHARS", ca.preview_chars),
    )


class GetConfig:
    def __init__(self):
        self.parse_cli_args()
        self._yaml = load_app_yaml()
        self._secrets = EnvSecrets()

    @property
    def yaml(self) -> AppYamlConfig:
        return self._yaml

    @lru_cache
    def get_app_config(self) -> AppSettings:
        return _build_app(self._secrets, self._yaml)

    @lru_cache
    def get_jwt_config(self) -> JwtSettings:
        return _build_jwt(self._secrets, self._yaml)

    @lru_cache
    def get_database_config(self) -> DataBaseSettings:
        return _build_database(self._secrets, self._yaml)

    @lru_cache
    def get_model_config(self) -> ModelSettings:
        return _build_model(self._secrets, self._yaml)

    @lru_cache
    def get_other_config(self) -> OtherSettings:
        return _build_other(self._yaml)

    @lru_cache
    def get_qdrant_config(self) -> QdrantSettings:
        return _build_qdrant(self._secrets, self._yaml)

    @lru_cache
    def get_stream_config(self) -> StreamSettings:
        return _build_stream(self._yaml)

    @lru_cache
    def get_langfuse_config(self) -> LangfuseSettings:
        return _build_langfuse(self._secrets, self._yaml)

    @lru_cache
    def get_web_tools_config(self) -> WebToolsSettings:
        return _build_web_tools(self._secrets, self._yaml)

    @lru_cache
    def get_checkpoint_config(self) -> CheckpointSettings:
        return _build_checkpoint(self._yaml)

    @lru_cache
    def get_chat_attachment_config(self) -> ChatAttachmentSettings:
        return _build_chat_attachment(self._yaml)

    @staticmethod
    def parse_cli_args() -> None:
        is_pytest = "pytest" in sys.modules or "pytest" in sys.argv[0]
        is_evals_cli = "evals/__main__" in sys.argv[0] or (
            len(sys.argv) > 1 and "--tag" in sys.argv[1:]
        )
        if "uvicorn" not in sys.argv[0] and not is_pytest and not is_evals_cli:
            parser = argparse.ArgumentParser(description="命令行参数")
            parser.add_argument("--env", type=str, default="", help="运行环境")
            args, _unknown = parser.parse_known_args()
            if args.env:
                os.environ["APP_ENV"] = args.env

        backend_dir = Path(__file__).resolve().parent.parent
        run_env = os.getenv("APP_ENV", "dev").strip().lower()
        for name in (f".env.{run_env}", ".env"):
            path = backend_dir / name
            if path.is_file():
                load_dotenv(path)
                return
        load_dotenv(backend_dir / ".env")


get_config = GetConfig()
AppConfig = get_config.get_app_config()
JwtConfig = get_config.get_jwt_config()
DataBaseConfig = get_config.get_database_config()
ModelConfig = get_config.get_model_config()
OtherConfig = get_config.get_other_config()
QdrantConfig = get_config.get_qdrant_config()
StreamConfig = get_config.get_stream_config()
LangfuseConfig = get_config.get_langfuse_config()
WebToolsConfig = get_config.get_web_tools_config()
CheckpointConfig = get_config.get_checkpoint_config()
ChatAttachmentConfig = get_config.get_chat_attachment_config()
