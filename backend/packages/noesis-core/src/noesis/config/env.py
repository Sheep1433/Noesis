"""配置入口：敏感项来自 .env，运行参数来自 config.yaml。"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv
from loguru import logger
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from noesis.config.yaml_config import AppYamlConfig, load_app_yaml
from noesis.config.paths import BACKEND_DIR


# ---------------------------------------------------------------------------
# .env：仅敏感 / 环境相关
# ---------------------------------------------------------------------------


class EnvSecrets(BaseSettings):
    """仅通过环境变量加载的敏感配置。"""

    model_config = SettingsConfigDict(extra="ignore")

    app_env: str = Field(default="dev", alias="APP_ENV")

    postgres_password: str = Field(default="postgres", alias="POSTGRES_PASSWORD")

    model_api_key: str = Field(default="", alias="MODEL_API_KEY")
    embedding_model_api_key: str = Field(default="", alias="EMBEDDING_MODEL_API_KEY")
    rerank_model_api_key: str = Field(default="", alias="RERANK_MODEL_API_KEY")
    vlm_model_api_key: str = Field(default="", alias="VLM_MODEL_API_KEY")

    qdrant_api_key: str = Field(default="", alias="QDRANT_API_KEY")

    langfuse_secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")
    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")

    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")

    sandbox_runner_token: str = Field(default="", alias="SANDBOX_RUNNER_TOKEN")
    settings_encryption_key: str = Field(default="", alias="SETTINGS_ENCRYPTION_KEY")
    feishu_app_id: str = Field(default="", alias="FEISHU_APP_ID")
    feishu_app_secret: str = Field(default="", alias="FEISHU_APP_SECRET")

    # 分布式 Run 协调（enable-distributed-sse-pubsub）：显式选择，禁止静默默认
    run_bus_backend: str = Field(default="", alias="NOESIS_RUN_BUS_BACKEND")
    redis_url: str = Field(default="", alias="REDIS_URL")
    cluster_id: str = Field(default="", alias="NOESIS_CLUSTER_ID")


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
class SessionSettings:
    idle_expire_days: int
    absolute_expire_days: int
    renewal_window_minutes: int
    cookie_name: str


@dataclass(frozen=True)
class DataBaseSettings:
    postgres_host: str
    postgres_port: int
    postgres_user: str
    postgres_password: str
    postgres_database: str
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
    embedding_model_name: str
    embedding_model_base_url: str
    embedding_model_api_key: str
    rerank_model_name: str
    rerank_model_base_url: str
    rerank_model_api_key: str
    vlm_model_name: str
    vlm_model_base_url: str
    vlm_model_api_key: str
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
    summarization_model_name: str
    summarization_model_temperature: float
    summarization_trigger_tokens: int
    summarization_trigger_fraction: float
    summarization_messages_to_keep: int
    governor_loop_enabled: bool
    governor_loop_hard_limit: int
    governor_loop_window_size: int
    tool_output_max_chars: int
    governor_tool_calls_enabled: bool
    governor_tool_calls_total: int | None
    governor_tool_calls_per_name: int | None
    # 用户自定义 Provider 平台预设（yaml model.provider_presets；dict 列表）
    provider_presets: tuple


@dataclass(frozen=True)
class StreamSettings:
    sse_keepalive_interval_seconds: float
    checkpoint_interval_seconds: float
    persistence_timeout_seconds: float
    persistence_retry_interval_seconds: float
    run_event_buffer_max_events: int
    run_event_buffer_max_bytes: int
    run_subscriber_queue_max_events: int
    run_subscriber_queue_max_bytes: int
    run_max_active: int
    run_max_active_per_user: int
    run_max_subscriptions_per_run: int
    run_max_subscriptions_per_user: int
    run_max_subscriptions_global: int
    run_terminal_retention_seconds: float
    run_max_duration_seconds: float
    run_max_duration_seconds_super_agent: float
    run_max_output_bytes: int
    run_hitl_pending_timeout_seconds: float
    run_shutdown_drain_seconds: float
    run_cancel_grace_seconds: float
    run_terminal_persistence_budget_seconds: float
    run_terminal_retry_interval_seconds: float
    run_tool_timeout_seconds: float
    run_channel_queue_max_batches: int
    run_channel_queue_max_bytes: int
    run_channel_drain_seconds: float


@dataclass(frozen=True)
class DistributedRunsSettings:
    backend: str
    redis_url: str
    cluster_id: str
    leader_heartbeat_seconds: float
    queued_scan_interval_seconds: float
    command_scan_interval_seconds: float
    publisher_queue_max_events: int
    publisher_queue_max_bytes: int
    handshake_buffer_max_events: int
    handshake_buffer_max_bytes: int
    reconciliation_interval_seconds: float
    periodic_checkpoint_interval_seconds: float
    envelope_payload_max_bytes: int
    redis_socket_timeout_seconds: float
    redis_connect_timeout_seconds: float
    redis_pool_max_connections: int


@dataclass(frozen=True)
class RetrievalLimitSettings:
    max_results_per_call: int
    max_results_per_run: int
    max_excerpt_chars: int
    max_excerpt_bytes: int
    max_locator_bytes: int


@dataclass(frozen=True)
class MemorySettings:
    extraction_model: str
    selection_model: str
    enabled_by_default: bool
    session_idle_minutes: int
    sweep_interval_minutes: int
    max_entries_per_extraction: int
    index_max_lines: int
    index_max_bytes: int
    stale_warning_days: int
    inject_budget_tokens: int
    max_entry_chars: int
    consolidation_min_interval_hours: int
    consolidation_min_new_sessions: int
    max_message_chars: int


@dataclass(frozen=True)
class HitlSettings:
    enabled: bool
    ask_timeout_seconds: int


@dataclass(frozen=True)
class SubagentSettings:
    max_concurrent_per_session: int
    task_timeout_seconds: float
    foreground_max_wait_seconds: float
    auto_continue: bool
    shell_task_timeout_seconds: float


@dataclass(frozen=True)
class MessagingSettings:
    telegram_runtime_enabled: bool
    telegram_poll_timeout_seconds: int
    feishu_runtime_enabled: bool
    feishu_app_id: str
    feishu_app_secret: str


@dataclass(frozen=True)
class OtherSettings:
    skills_filesystem_root: str
    mcp_config_path: str


@dataclass(frozen=True)
class SkillsMarketFeaturedSkill:
    id: str
    source: str
    skill_id: str


@dataclass(frozen=True)
class SkillsMarketSettings:
    provider: str
    base_url: str
    search_timeout_seconds: int
    github_timeout_seconds: int
    cache_ttl_seconds: int
    preview_cache_ttl_seconds: int
    max_archive_bytes: int
    featured_skills: tuple[SkillsMarketFeaturedSkill, ...]


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
    ddg_backends: str
    tavily_api_key: str


@dataclass(frozen=True)
class SandboxSettings:
    backend: str
    runner_url: str
    execute_timeout_seconds: int


@dataclass(frozen=True)
class CheckpointSettings:
    postgres_database: str


@dataclass(frozen=True)
class ChatAttachmentSettings:
    enabled: bool
    ttl_days: int
    max_file_mb: int
    auto_convert: bool
    max_image_mb: int
    vision_enabled: bool
    reinject_session_images: bool
    max_files_per_message: int
    image_inject_max_edge: int
    vlm_fallback_enabled: bool
    tiny_inline_chars: int
    read_page_lines: int
    preview_chars: int


@dataclass(frozen=True)
class KbSettings:
    deepdoc_enabled: bool
    deepdoc_model_dir: str
    parser_default: str


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


def _build_session(yaml_cfg: AppYamlConfig) -> SessionSettings:
    session = yaml_cfg.session
    return SessionSettings(
        idle_expire_days=_legacy_env_int(
            "SESSION_IDLE_EXPIRE_DAYS", session.idle_expire_days
        ),
        absolute_expire_days=_legacy_env_int(
            "SESSION_ABSOLUTE_EXPIRE_DAYS", session.absolute_expire_days
        ),
        renewal_window_minutes=_legacy_env_int(
            "SESSION_RENEWAL_WINDOW_MINUTES", session.renewal_window_minutes
        ),
        cookie_name=_legacy_env("SESSION_COOKIE_NAME", session.cookie_name),
    )


def _build_database(secrets: EnvSecrets, yaml_cfg: AppYamlConfig) -> DataBaseSettings:
    db = yaml_cfg.database
    return DataBaseSettings(
        postgres_host=_legacy_env("POSTGRES_HOST", db.host),
        postgres_port=_legacy_env_int("POSTGRES_PORT", db.port),
        postgres_user=_legacy_env("POSTGRES_USER", db.user),
        postgres_password=secrets.postgres_password,
        postgres_database=_legacy_env("POSTGRES_DATABASE", db.database),
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
    emb = yaml_cfg.embedding
    rerank = yaml_cfg.rerank
    vlm = yaml_cfg.vlm
    runtime = yaml_cfg.agent_runtime
    gov = runtime.governor
    vlm_api_key = (
        secrets.vlm_model_api_key
        or _legacy_env("VL_MODEL_API_KEY", "")
        or secrets.embedding_model_api_key
        or ""
    ).strip()
    rerank_api_key = (
        secrets.rerank_model_api_key or secrets.embedding_model_api_key or ""
    ).strip()
    return ModelSettings(
        provider_presets=tuple(
            {
                "id": preset.id,
                "label": preset.label or preset.id,
                "base_url": preset.base_url,
                "headers": dict(preset.headers),
                "reasoning_levels": list(preset.reasoning_levels),
            }
            for preset in m.provider_presets
            if preset.id and preset.base_url
        ),
        model_type=_legacy_env("MODEL_TYPE", m.type),
        model_name=_legacy_env("MODEL_NAME", m.name),
        model_temperature=_legacy_env_float("MODEL_TEMPERATURE", m.temperature),
        model_base_url=_legacy_env("MODEL_BASE_URL", m.base_url),
        model_api_key=secrets.model_api_key or m.api_key,
        embedding_model_name=_legacy_env("EMBEDDING_MODEL_NAME", emb.name),
        embedding_model_base_url=_legacy_env("EMBEDDING_MODEL_BASE_URL", emb.base_url),
        embedding_model_api_key=secrets.embedding_model_api_key,
        rerank_model_name=_legacy_env("RERANK_MODEL_NAME", rerank.name),
        rerank_model_base_url=_legacy_env("RERANK_MODEL_BASE_URL", rerank.base_url),
        rerank_model_api_key=rerank_api_key,
        vlm_model_name=_legacy_env("VLM_MODEL_NAME", vlm.name),
        vlm_model_base_url=_legacy_env("VLM_MODEL_BASE_URL", vlm.base_url),
        vlm_model_api_key=vlm_api_key,
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
        context_max_input_tokens=_legacy_env_int(
            "CONTEXT_MAX_INPUT_TOKENS", ctx.max_input_tokens
        ),
        context_display_enabled=_legacy_env_bool(
            "CONTEXT_DISPLAY_ENABLED", ctx.display_enabled
        ),
        summarization_enabled=_legacy_env_bool("SUMMARIZATION_ENABLED", s.enabled),
        summarization_model_name=_legacy_env("SUMMARIZATION_MODEL_NAME", s.model_name),
        summarization_model_temperature=_legacy_env_float(
            "SUMMARIZATION_MODEL_TEMPERATURE", s.temperature
        ),
        summarization_trigger_tokens=_legacy_env_int(
            "SUMMARIZATION_TRIGGER_TOKENS", s.trigger_tokens
        ),
        summarization_trigger_fraction=_legacy_env_float(
            "SUMMARIZATION_TRIGGER_FRACTION", s.trigger_fraction
        ),
        summarization_messages_to_keep=_legacy_env_int(
            "SUMMARIZATION_MESSAGES_TO_KEEP", s.messages_to_keep
        ),
        governor_loop_enabled=_legacy_env_bool(
            "GOVERNOR_LOOP_ENABLED", gov.loop_enabled
        ),
        governor_loop_hard_limit=_legacy_env_int(
            "GOVERNOR_LOOP_HARD_LIMIT", gov.loop_hard_limit
        ),
        governor_loop_window_size=_legacy_env_int(
            "GOVERNOR_LOOP_WINDOW_SIZE", gov.loop_window_size
        ),
        tool_output_max_chars=_legacy_env_int(
            "TOOL_OUTPUT_MAX_CHARS", runtime.tool_output_max_chars
        ),
        governor_tool_calls_enabled=_legacy_env_bool(
            "GOVERNOR_TOOL_CALLS_ENABLED", gov.tool_calls_enabled
        ),
        governor_tool_calls_total=(
            _legacy_env_int("GOVERNOR_TOOL_CALLS_TOTAL", gov.tool_calls_total or 0)
            or None
        ),
        governor_tool_calls_per_name=(
            _legacy_env_int(
                "GOVERNOR_TOOL_CALLS_PER_NAME",
                gov.tool_calls_per_name or 0,
            )
            or None
        ),
    )


_CLUSTER_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


def _build_distributed_runs(
    secrets: EnvSecrets, yaml_cfg: AppYamlConfig
) -> DistributedRunsSettings:
    """Run bus 后端与集群标识：显式选择、错误配置 fail-fast（禁止自动 fallback）。"""
    dr = yaml_cfg.distributed_runs
    backend = secrets.run_bus_backend.strip().lower()
    if not backend:
        raise ValueError(
            "NOESIS_RUN_BUS_BACKEND 必填：显式设置为 memory 或 redis（不提供静默默认）"
        )
    if backend not in ("memory", "redis"):
        raise ValueError(
            f"NOESIS_RUN_BUS_BACKEND 非法值 {secrets.run_bus_backend!r}：仅支持 memory|redis"
        )

    redis_url = secrets.redis_url.strip()
    cluster_id = secrets.cluster_id.strip()
    if backend == "redis":
        if not redis_url:
            raise ValueError("run bus backend=redis 时 REDIS_URL 必填")
        if not cluster_id:
            raise ValueError("run bus backend=redis 时 NOESIS_CLUSTER_ID 必填")
        if not _CLUSTER_ID_PATTERN.fullmatch(cluster_id):
            raise ValueError(
                "NOESIS_CLUSTER_ID 仅允许字母、数字、下划线、连字符"
            )
    else:
        # memory 模式写入本地稳定 cluster_id，与 redis 模式保持同构；
        # 残留的 Redis 配置只告警一次，不参与运行
        if redis_url or cluster_id:
            logger.warning(
                "run bus backend=memory，REDIS_URL/NOESIS_CLUSTER_ID 配置被忽略"
            )
        cluster_id = cluster_id or "local"

    return DistributedRunsSettings(
        backend=backend,
        redis_url=redis_url,
        cluster_id=cluster_id,
        leader_heartbeat_seconds=dr.leader_heartbeat_seconds,
        queued_scan_interval_seconds=dr.queued_scan_interval_seconds,
        command_scan_interval_seconds=dr.command_scan_interval_seconds,
        publisher_queue_max_events=dr.publisher_queue_max_events,
        publisher_queue_max_bytes=dr.publisher_queue_max_bytes,
        handshake_buffer_max_events=dr.handshake_buffer_max_events,
        handshake_buffer_max_bytes=dr.handshake_buffer_max_bytes,
        reconciliation_interval_seconds=dr.reconciliation_interval_seconds,
        periodic_checkpoint_interval_seconds=dr.periodic_checkpoint_interval_seconds,
        envelope_payload_max_bytes=dr.envelope_payload_max_bytes,
        redis_socket_timeout_seconds=dr.redis_socket_timeout_seconds,
        redis_connect_timeout_seconds=dr.redis_connect_timeout_seconds,
        redis_pool_max_connections=dr.redis_pool_max_connections,
    )


def _build_stream(yaml_cfg: AppYamlConfig) -> StreamSettings:
    stream = yaml_cfg.stream
    return StreamSettings(
        sse_keepalive_interval_seconds=_legacy_env_float(
            "SSE_KEEPALIVE_INTERVAL_SECONDS", stream.sse_keepalive_interval_seconds
        ),
        checkpoint_interval_seconds=stream.checkpoint_interval_seconds,
        persistence_timeout_seconds=stream.persistence_timeout_seconds,
        persistence_retry_interval_seconds=stream.persistence_retry_interval_seconds,
        run_event_buffer_max_events=stream.run_event_buffer_max_events,
        run_event_buffer_max_bytes=stream.run_event_buffer_max_bytes,
        run_subscriber_queue_max_events=stream.run_subscriber_queue_max_events,
        run_subscriber_queue_max_bytes=stream.run_subscriber_queue_max_bytes,
        run_max_active=stream.run_max_active,
        run_max_active_per_user=stream.run_max_active_per_user,
        run_max_subscriptions_per_run=stream.run_max_subscriptions_per_run,
        run_max_subscriptions_per_user=stream.run_max_subscriptions_per_user,
        run_max_subscriptions_global=stream.run_max_subscriptions_global,
        run_terminal_retention_seconds=stream.run_terminal_retention_seconds,
        run_max_duration_seconds=stream.run_max_duration_seconds,
        run_max_duration_seconds_super_agent=stream.run_max_duration_seconds_super_agent,
        run_max_output_bytes=stream.run_max_output_bytes,
        run_hitl_pending_timeout_seconds=stream.run_hitl_pending_timeout_seconds,
        run_shutdown_drain_seconds=stream.run_shutdown_drain_seconds,
        run_cancel_grace_seconds=stream.run_cancel_grace_seconds,
        run_terminal_persistence_budget_seconds=stream.run_terminal_persistence_budget_seconds,
        run_terminal_retry_interval_seconds=stream.run_terminal_retry_interval_seconds,
        run_tool_timeout_seconds=stream.run_tool_timeout_seconds,
        run_channel_queue_max_batches=stream.run_channel_queue_max_batches,
        run_channel_queue_max_bytes=stream.run_channel_queue_max_bytes,
        run_channel_drain_seconds=stream.run_channel_drain_seconds,
    )


def _build_retrieval_limits(yaml_cfg: AppYamlConfig) -> RetrievalLimitSettings:
    limits = yaml_cfg.retrieval_limits
    return RetrievalLimitSettings(
        max_results_per_call=limits.max_results_per_call,
        max_results_per_run=limits.max_results_per_run,
        max_excerpt_chars=limits.max_excerpt_chars,
        max_excerpt_bytes=limits.max_excerpt_bytes,
        max_locator_bytes=limits.max_locator_bytes,
    )




def _build_memory(yaml_cfg: AppYamlConfig) -> MemorySettings:
    value = yaml_cfg.memory
    return MemorySettings(
        extraction_model=value.extraction_model.strip(),
        selection_model=value.selection_model.strip(),
        enabled_by_default=value.enabled_by_default,
        session_idle_minutes=value.session_idle_minutes,
        sweep_interval_minutes=value.sweep_interval_minutes,
        max_entries_per_extraction=value.max_entries_per_extraction,
        index_max_lines=value.index_max_lines,
        index_max_bytes=value.index_max_bytes,
        stale_warning_days=value.stale_warning_days,
        inject_budget_tokens=value.inject_budget_tokens,
        max_entry_chars=value.max_entry_chars,
        consolidation_min_interval_hours=value.consolidation_min_interval_hours,
        consolidation_min_new_sessions=value.consolidation_min_new_sessions,
        max_message_chars=value.max_message_chars,
    )


def _build_hitl(yaml_cfg: AppYamlConfig) -> HitlSettings:
    hitl = yaml_cfg.hitl
    return HitlSettings(
        enabled=hitl.enabled,
        ask_timeout_seconds=hitl.ask_timeout_seconds,
    )


def _build_subagents(yaml_cfg: AppYamlConfig) -> SubagentSettings:
    subagents = yaml_cfg.subagents
    return SubagentSettings(
        max_concurrent_per_session=subagents.max_concurrent_per_session,
        task_timeout_seconds=subagents.task_timeout_seconds,
        foreground_max_wait_seconds=subagents.foreground_max_wait_seconds,
        auto_continue=subagents.auto_continue,
        shell_task_timeout_seconds=subagents.shell_task_timeout_seconds,
    )


def _build_messaging(secrets: EnvSecrets, yaml_cfg: AppYamlConfig) -> MessagingSettings:
    m = yaml_cfg.messaging
    return MessagingSettings(
        telegram_runtime_enabled=m.telegram_runtime_enabled,
        telegram_poll_timeout_seconds=m.telegram_poll_timeout_seconds,
        feishu_runtime_enabled=m.feishu_runtime_enabled,
        feishu_app_id=secrets.feishu_app_id,
        feishu_app_secret=secrets.feishu_app_secret,
    )


def _build_other(yaml_cfg: AppYamlConfig) -> OtherSettings:
    other = yaml_cfg.other
    return OtherSettings(
        skills_filesystem_root=_legacy_env(
            "SKILLS_FILESYSTEM_ROOT", other.skills_filesystem_root
        ),
        mcp_config_path=_legacy_env("MCP_CONFIG_PATH", other.mcp_config_path),
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
        qdrant_default_collection=_legacy_env(
            "QDRANT_DEFAULT_COLLECTION", q.default_collection
        ),
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
        langfuse_tracing_enabled=_legacy_env_bool(
            "LANGFUSE_TRACING_ENABLED", lf.tracing_enabled
        ),
        langfuse_secret_key=secrets.langfuse_secret_key,
        langfuse_public_key=secrets.langfuse_public_key,
        langfuse_base_url=_legacy_env("LANGFUSE_BASE_URL", lf.base_url),
    )


def _build_skills_market(yaml_cfg: AppYamlConfig) -> SkillsMarketSettings:
    sm = yaml_cfg.skills_market
    featured = tuple(
        SkillsMarketFeaturedSkill(
            id=(item.id or "").strip(),
            source=(item.source or "").strip(),
            skill_id=(item.skill_id or "").strip(),
        )
        for item in sm.featured_skills
        if (item.source or "").strip() and (item.skill_id or "").strip()
    )
    return SkillsMarketSettings(
        provider=sm.provider.strip() or "skills_sh",
        base_url=sm.base_url.strip().rstrip("/") or "https://skills.sh",
        search_timeout_seconds=sm.search_timeout_seconds,
        github_timeout_seconds=sm.github_timeout_seconds,
        cache_ttl_seconds=sm.cache_ttl_seconds,
        preview_cache_ttl_seconds=sm.preview_cache_ttl_seconds,
        max_archive_bytes=sm.max_archive_bytes,
        featured_skills=featured,
    )


def _build_web_tools(secrets: EnvSecrets, yaml_cfg: AppYamlConfig) -> WebToolsSettings:
    wt = yaml_cfg.web_tools
    return WebToolsSettings(
        max_search_results=_legacy_env_int(
            "WEB_MAX_SEARCH_RESULTS", wt.max_search_results
        ),
        fetch_max_chars=_legacy_env_int("WEB_FETCH_MAX_CHARS", wt.fetch_max_chars),
        fetch_timeout_seconds=_legacy_env_int(
            "WEB_FETCH_TIMEOUT_SECONDS", wt.fetch_timeout_seconds
        ),
        ddg_backends=_legacy_env("WEB_DDG_BACKENDS", wt.ddg_backends).strip()
        or "mojeek,yandex",
        tavily_api_key=secrets.tavily_api_key,
    )


def _build_sandbox(secrets: EnvSecrets, yaml_cfg: AppYamlConfig) -> SandboxSettings:
    sb = yaml_cfg.sandbox
    backend = _legacy_env("SANDBOX_BACKEND", sb.backend).strip().lower() or "docker"
    if backend == "aio":
        raise ValueError("sandbox.backend=aio 已移除；请使用 docker 或 local_shell")
    if backend not in ("docker", "local_shell"):
        backend = "docker"
    return SandboxSettings(
        backend=backend,
        runner_url=_legacy_env("SANDBOX_RUNNER_URL", sb.runner_url),
        execute_timeout_seconds=_legacy_env_int(
            "SANDBOX_EXECUTE_TIMEOUT_SECONDS", sb.execute_timeout_seconds
        ),
    )


def get_sandbox_runner_token(secrets: EnvSecrets | None = None) -> str:
    """runner 鉴权 token（仅 .env，不进 config.yaml）。"""
    if secrets is None:
        secrets = EnvSecrets()
    return secrets.sandbox_runner_token or _legacy_env("SANDBOX_RUNNER_TOKEN", "")


def sandbox_runner_headers() -> dict[str, str]:
    """sandbox-runner 内网 API 鉴权头。"""
    headers: dict[str, str] = {}
    token = get_sandbox_runner_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _build_checkpoint(yaml_cfg: AppYamlConfig) -> CheckpointSettings:
    cp = yaml_cfg.checkpoint
    return CheckpointSettings(
        postgres_database=_legacy_env("LANGGRAPH_POSTGRES_DATABASE", cp.database),
    )


def _build_chat_attachment(yaml_cfg: AppYamlConfig) -> ChatAttachmentSettings:
    ca = yaml_cfg.chat_attachment
    settings = ChatAttachmentSettings(
        enabled=_legacy_env_bool("CHAT_ATTACHMENT_ENABLED", ca.enabled),
        ttl_days=_legacy_env_int("CHAT_ATTACHMENT_TTL_DAYS", ca.ttl_days),
        max_file_mb=_legacy_env_int("CHAT_ATTACHMENT_MAX_FILE_MB", ca.max_file_mb),
        auto_convert=_legacy_env_bool("CHAT_ATTACHMENT_AUTO_CONVERT", ca.auto_convert),
        max_image_mb=_legacy_env_int("CHAT_ATTACHMENT_MAX_IMAGE_MB", ca.max_image_mb),
        vision_enabled=_legacy_env_bool(
            "CHAT_ATTACHMENT_VISION_ENABLED", ca.vision_enabled
        ),
        reinject_session_images=_legacy_env_bool(
            "CHAT_ATTACHMENT_REINJECT_SESSION_IMAGES", ca.reinject_session_images
        ),
        max_files_per_message=_legacy_env_int(
            "CHAT_ATTACHMENT_MAX_FILES_PER_MESSAGE", ca.max_files_per_message
        ),
        image_inject_max_edge=_legacy_env_int(
            "CHAT_ATTACHMENT_IMAGE_INJECT_MAX_EDGE", ca.image_inject_max_edge
        ),
        vlm_fallback_enabled=_legacy_env_bool(
            "CHAT_ATTACHMENT_VLM_FALLBACK_ENABLED", ca.vlm_fallback_enabled
        ),
        tiny_inline_chars=_legacy_env_int(
            "CHAT_ATTACHMENT_TINY_INLINE_CHARS", ca.tiny_inline_chars
        ),
        read_page_lines=_legacy_env_int(
            "CHAT_ATTACHMENT_READ_PAGE_LINES", ca.read_page_lines
        ),
        preview_chars=_legacy_env_int(
            "CHAT_ATTACHMENT_PREVIEW_CHARS", ca.preview_chars
        ),
    )
    return settings


def _build_kb(yaml_cfg: AppYamlConfig) -> KbSettings:
    kb = yaml_cfg.kb
    return KbSettings(
        deepdoc_enabled=_legacy_env_bool("KB_DEEPDOC_ENABLED", kb.deepdoc.enabled),
        deepdoc_model_dir=_legacy_env("KB_DEEPDOC_MODEL_DIR", kb.deepdoc.model_dir),
        parser_default=_legacy_env("KB_PARSER_DEFAULT", kb.parser.default)
        .strip()
        .lower()
        or "deepdoc",
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
    def get_session_config(self) -> SessionSettings:
        return _build_session(self._yaml)

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
    def get_distributed_runs_config(self) -> DistributedRunsSettings:
        return _build_distributed_runs(self._secrets, self._yaml)

    @lru_cache
    def get_retrieval_limit_config(self) -> RetrievalLimitSettings:
        return _build_retrieval_limits(self._yaml)

    @lru_cache
    def get_langfuse_config(self) -> LangfuseSettings:
        return _build_langfuse(self._secrets, self._yaml)

    @lru_cache
    def get_skills_market_config(self) -> SkillsMarketSettings:
        return _build_skills_market(self._yaml)

    @lru_cache
    def get_web_tools_config(self) -> WebToolsSettings:
        return _build_web_tools(self._secrets, self._yaml)

    @lru_cache
    def get_sandbox_config(self) -> SandboxSettings:
        return _build_sandbox(self._secrets, self._yaml)

    @lru_cache
    def get_hitl_config(self) -> HitlSettings:
        return _build_hitl(self._yaml)

    @lru_cache
    def get_subagent_config(self) -> SubagentSettings:
        return _build_subagents(self._yaml)

    @lru_cache
    def get_messaging_config(self) -> MessagingSettings:
        return _build_messaging(self._secrets, self._yaml)

    @lru_cache
    def get_checkpoint_config(self) -> CheckpointSettings:
        return _build_checkpoint(self._yaml)

    @lru_cache
    def get_chat_attachment_config(self) -> ChatAttachmentSettings:
        return _build_chat_attachment(self._yaml)

    @lru_cache
    def get_kb_config(self) -> KbSettings:
        return _build_kb(self._yaml)

    @lru_cache
    def get_memory_config(self) -> MemorySettings:
        return _build_memory(self._yaml)

    @staticmethod
    def parse_cli_args() -> None:
        is_pytest = "pytest" in sys.modules or "pytest" in sys.argv[0]
        is_evals_cli = any(
            marker in sys.argv[0]
            for marker in (
                "evals/__main__",
                "evals/agent",
                "evals/case",
                "evals/compression",
            )
        ) or (len(sys.argv) > 1 and "--tag" in sys.argv[1:])
        if "uvicorn" not in sys.argv[0] and not is_pytest and not is_evals_cli:
            parser = argparse.ArgumentParser(description="命令行参数")
            parser.add_argument("--env", type=str, default="", help="运行环境")
            args, _unknown = parser.parse_known_args()
            if args.env:
                os.environ["APP_ENV"] = args.env

        backend_dir = BACKEND_DIR
        run_env = os.getenv("APP_ENV", "dev").strip().lower()
        for name in (f".env.{run_env}", ".env"):
            path = backend_dir / name
            if path.is_file():
                load_dotenv(path)
                return
        load_dotenv(backend_dir / ".env")


get_config = GetConfig()
AppConfig = get_config.get_app_config()
SessionConfig = get_config.get_session_config()
DataBaseConfig = get_config.get_database_config()
ModelConfig = get_config.get_model_config()
OtherConfig = get_config.get_other_config()
QdrantConfig = get_config.get_qdrant_config()
StreamConfig = get_config.get_stream_config()
DistributedRunsConfig = get_config.get_distributed_runs_config()
RetrievalLimitConfig = get_config.get_retrieval_limit_config()
LangfuseConfig = get_config.get_langfuse_config()
SkillsMarketConfig = get_config.get_skills_market_config()
WebToolsConfig = get_config.get_web_tools_config()
CheckpointConfig = get_config.get_checkpoint_config()
SandboxConfig = get_config.get_sandbox_config()
HitlConfig = get_config.get_hitl_config()
SubagentConfig = get_config.get_subagent_config()
MessagingConfig = get_config.get_messaging_config()
ChatAttachmentConfig = get_config.get_chat_attachment_config()
KbConfig = get_config.get_kb_config()
MemoryConfig = get_config.get_memory_config()
