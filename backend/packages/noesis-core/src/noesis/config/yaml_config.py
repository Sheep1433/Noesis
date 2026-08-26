"""非敏感运行时配置：从 config.yaml 加载。"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from noesis.config.paths import BACKEND_DIR
from noesis.runtime.logging import logger


def _backend_dir() -> Path:
    return BACKEND_DIR


def resolve_config_path() -> Path:
    if custom := os.getenv("NOESIS_CONFIG_PATH"):
        return Path(custom).expanduser().resolve()

    backend = _backend_dir()
    app_env = os.getenv("APP_ENV", "dev").strip().lower()
    if app_env == "prod":
        prod_path = backend / "config.prod.yaml"
        if prod_path.is_file():
            return prod_path
    return backend / "config.yaml"


def resolve_env_variables(value: Any) -> Any:
    """支持 ``$ENV_VAR`` 环境变量引用。"""
    if isinstance(value, str):
        if value.startswith("$"):
            env_name = value[1:]
            resolved = os.getenv(env_name)
            if resolved is None:
                raise ValueError(f"环境变量 {env_name} 未设置（config 引用 {value}）")
            return resolved
        return value
    if isinstance(value, dict):
        return {k: resolve_env_variables(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_env_variables(item) for item in value]
    return value


class AppYamlSection(BaseModel):
    name: str = "Noesis-FastAPI"
    root_path: str = ""
    host: str = "0.0.0.0"
    port: int = 8089
    version: str = "1.0.0"
    reload: bool = True


class SessionYamlSection(BaseModel):
    idle_expire_days: int = Field(default=30, ge=1)
    absolute_expire_days: int = Field(default=90, ge=1)
    renewal_window_minutes: int = Field(default=5, ge=1)
    cookie_name: str = "noesis_session"


class DatabaseYamlSection(BaseModel):
    host: str = "127.0.0.1"
    port: int = 5432
    user: str = "noesis"
    database: str = "noesis"
    echo: bool = True
    max_overflow: int = 10
    pool_size: int = 50
    pool_recycle: int = 3600
    pool_timeout: int = 30


class ModelGenerationYamlSection(BaseModel):
    max_tokens: int = Field(default=32000, ge=1)
    top_p: float = 0.8
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    streaming: bool = True


class ModelCatalogEntryYamlSection(BaseModel):
    """可选对话模型；未填字段继承 model 层默认。"""

    id: str = ""
    label: str = ""
    type: str = ""
    name: str = ""
    temperature: float | None = None
    base_url: str = ""
    # 上下文窗口（圆环分母 / 压缩阈值）；0=未配置，继承 model 层或 fallback 默认
    context_window: int = Field(default=0, ge=0)


class ProviderPresetYamlSection(BaseModel):
    """用户自定义 Provider 的平台预设（dsh catalog provider 模式）：选预设只填 Key。"""

    id: str = ""
    label: str = ""
    base_url: str = ""
    # 归因类 header；敏感凭证不放预设，用户 Key 仍走加密存储
    headers: dict[str, str] = Field(default_factory=dict)


class ModelYamlSection(BaseModel):
    """主对话 LLM：type / name / base_url / api_key。

    api_key 承载非机密平台 Key（OpenCode Zen 公开值为 public）；
    机密 Key 仍走 .env MODEL_API_KEY（env 优先于 yaml）。
    """

    type: str = "qwen"
    name: str = "qwen-plus"
    temperature: float = 0.75
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: str = ""
    show_thinking_process: bool = True
    request_timeout: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=2, ge=0)
    generation: ModelGenerationYamlSection = Field(
        default_factory=ModelGenerationYamlSection
    )
    default_catalog_id: str = ""
    # 上下文窗口（model 层默认，catalog 条目未配时继承）；0=未配置
    context_window: int = Field(default=0, ge=0)
    catalog: list[ModelCatalogEntryYamlSection] = Field(default_factory=list)
    # 用户自定义 Provider 的平台预设目录（部署者维护；透出给前端快速填充）
    provider_presets: list[ProviderPresetYamlSection] = Field(default_factory=list)


class RemoteModelYamlSection(BaseModel):
    """远程模型端点：name + base_url；api_key 在 .env 对应变量。"""

    name: str = ""
    base_url: str = ""


class EmbeddingYamlSection(RemoteModelYamlSection):
    name: str = "text-embedding-v4"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class RerankYamlSection(RemoteModelYamlSection):
    name: str = "gte-rerank-v2"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class VlmYamlSection(RemoteModelYamlSection):
    name: str = "Qwen3-VL-32B-Instruct"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class ContextYamlSection(BaseModel):
    # 摘要触发窗口覆盖值；0 表示使用当前 model catalog 的 limit.context
    max_input_tokens: int = Field(default=0, ge=0)
    display_enabled: bool = True


class SummarizationYamlSection(BaseModel):
    enabled: bool = True
    # 仅模型名单独配置；type / base_url / api_key 与 model 层一致
    model_name: str = ""
    temperature: float = 0.0
    # 自动压缩触发阈值。trigger_tokens > 0 时用绝对 token（距 effective_limit 顶部的余量）优先；
    # 为 0 时用 trigger_fraction × effective_limit（对齐 hermes compression.threshold）：
    # fraction=0.75 表示请求 token 达 effective_limit 的 75% 时触发压缩。
    trigger_tokens: int = Field(default=0, ge=0)
    trigger_fraction: float = Field(default=0.75, gt=0, le=1)
    # model profile 不可用时的消息数量 fallback
    messages_to_keep: int = Field(default=28, ge=1)
    # model profile 不可用时的消息数量 fallback
    messages_to_keep: int = Field(default=28, ge=1)


class GovernorYamlSection(BaseModel):
    # 工具调用总量与单工具上限；tool_calls_enabled=false 时两项均不生效
    tool_calls_enabled: bool = Field(default=True)
    tool_calls_total: int | None = Field(default=None, ge=1)
    tool_calls_per_name: int | None = Field(default=10, ge=1)
    # 同一 run 内的重复工具循环检测
    loop_enabled: bool = Field(default=True)
    loop_hard_limit: int = Field(default=5, ge=2)
    loop_window_size: int = Field(default=20, ge=1)


class AgentRuntimeYamlSection(BaseModel):
    tool_output_max_chars: int = Field(default=24_000, ge=1)
    governor: GovernorYamlSection = Field(default_factory=GovernorYamlSection)


class RetrievalLimitsYamlSection(BaseModel):
    max_results_per_call: int = Field(default=30, ge=1)
    max_results_per_run: int = Field(default=500, ge=1)
    max_excerpt_chars: int = Field(default=2000, ge=1)
    max_excerpt_bytes: int = Field(default=8192, ge=1)
    max_locator_bytes: int = Field(default=2048, ge=1)


class StreamYamlSection(BaseModel):
    sse_keepalive_interval_seconds: float = Field(default=25.0, ge=0)
    checkpoint_interval_seconds: float = Field(default=2.0, gt=0)
    persistence_timeout_seconds: float = Field(default=5.0, gt=0)
    persistence_retry_interval_seconds: float = Field(default=0.25, gt=0)
    run_event_buffer_max_events: int = Field(default=2000, gt=0)
    run_event_buffer_max_bytes: int = Field(default=4 * 1024 * 1024, gt=0)
    run_subscriber_queue_max_events: int = Field(default=512, gt=0)
    run_subscriber_queue_max_bytes: int = Field(default=1024 * 1024, gt=0)
    run_max_active: int = Field(default=100, gt=0)
    run_max_active_per_user: int = Field(default=4, gt=0)
    run_max_subscriptions_per_run: int = Field(default=8, gt=0)
    run_max_subscriptions_per_user: int = Field(default=16, gt=0)
    run_max_subscriptions_global: int = Field(default=500, gt=0)
    run_terminal_retention_seconds: float = Field(default=300.0, ge=0)
    run_max_duration_seconds: float = Field(default=900.0, gt=0)
    run_max_duration_seconds_super_agent: float = Field(default=1800.0, gt=0)
    run_max_output_bytes: int = Field(default=16 * 1024 * 1024, gt=0)
    run_hitl_pending_timeout_seconds: float = Field(default=86400.0, gt=0)
    run_shutdown_drain_seconds: float = Field(default=10.0, ge=0)
    run_cancel_grace_seconds: float = Field(default=2.0, ge=0)
    run_terminal_persistence_budget_seconds: float = Field(default=5.0, gt=0)
    run_terminal_retry_interval_seconds: float = Field(default=5.0, gt=0)
    run_tool_timeout_seconds: float = Field(default=120.0, gt=0)
    run_channel_queue_max_batches: int = Field(default=128, gt=0)
    run_channel_queue_max_bytes: int = Field(default=1024 * 1024, gt=0)
    run_channel_drain_seconds: float = Field(default=5.0, gt=0)


class QdrantYamlSection(BaseModel):
    host: str = "localhost"
    port: int = 6333
    timeout: int = 5
    grpc_port: int = 6334
    prefer_grpc: bool = False
    default_collection: str = "knowledge_base"
    requirement_docs_collection: str = "requirement_docs"
    test_case_docs_collection: str = "test_case_docs"
    test_case_upload_collection: str = ""
    case_rag_historical_requirements_enabled: bool = False


class LangfuseYamlSection(BaseModel):
    tracing_enabled: bool = False
    base_url: str = ""


class OtherYamlSection(BaseModel):
    skills_filesystem_root: str = ""
    mcp_config_path: str = ""  # 空则默认 extensions/mcp/mcp.json


class SkillsMarketFeaturedItem(BaseModel):
    id: str = ""
    source: str = ""
    skill_id: str = ""


class SkillsMarketYamlSection(BaseModel):
    """skills.sh 市场：搜索发现 + GitHub 安装。"""

    provider: str = "skills_sh"
    base_url: str = "https://skills.sh"
    search_timeout_seconds: int = Field(default=15, ge=1)
    github_timeout_seconds: int = Field(default=60, ge=1)
    cache_ttl_seconds: int = Field(default=300, ge=0)
    preview_cache_ttl_seconds: int = Field(default=86400, ge=0)
    max_archive_bytes: int = Field(default=50 * 1024 * 1024, ge=1)
    featured_skills: list[SkillsMarketFeaturedItem] = Field(
        default_factory=lambda: [
            SkillsMarketFeaturedItem(
                id="vercel-labs/skills/find-skills",
                source="vercel-labs/skills",
                skill_id="find-skills",
            ),
            SkillsMarketFeaturedItem(
                id="anthropics/skills/pdf",
                source="anthropics/skills",
                skill_id="pdf",
            ),
            SkillsMarketFeaturedItem(
                id="anthropics/skills/frontend-design",
                source="anthropics/skills",
                skill_id="frontend-design",
            ),
            SkillsMarketFeaturedItem(
                id="anthropics/skills/docx",
                source="anthropics/skills",
                skill_id="docx",
            ),
            SkillsMarketFeaturedItem(
                id="anthropics/skills/xlsx",
                source="anthropics/skills",
                skill_id="xlsx",
            ),
            SkillsMarketFeaturedItem(
                id="anthropics/skills/pptx",
                source="anthropics/skills",
                skill_id="pptx",
            ),
            SkillsMarketFeaturedItem(
                id="obra/superpowers/brainstorming",
                source="obra/superpowers",
                skill_id="brainstorming",
            ),
            SkillsMarketFeaturedItem(
                id="vercel-labs/agent-skills/vercel-react-best-practices",
                source="vercel-labs/agent-skills",
                skill_id="vercel-react-best-practices",
            ),
        ],
    )


class WebToolsYamlSection(BaseModel):
    max_search_results: int = Field(default=8, ge=1, le=20)
    fetch_max_chars: int = Field(default=4096, ge=1)
    fetch_timeout_seconds: int = Field(default=30, ge=1)
    # DDG 回退时使用的引擎列表（逗号分隔）；避免 auto 轮询不可达源导致 N×timeout 延迟
    ddg_backends: str = Field(default="mojeek,yandex")


class CheckpointYamlSection(BaseModel):
    database: str = "noesis_langgraph"


class SandboxYamlSection(BaseModel):
    # docker：slim 镜像 + docker exec（生产）；local_shell：宿主机（开发/测试）
    backend: str = "docker"
    runner_url: str = "http://127.0.0.1:8090"
    execute_timeout_seconds: int = Field(default=120, ge=1)


class HitlYamlSection(BaseModel):
    """SuperAgent 人机协同（工具审批 / ask_user）。"""

    enabled: bool = False
    # 默认 24h：异步审批场景避免短超时误 reject
    ask_timeout_seconds: int = Field(default=86400, ge=1)


class SubagentsYamlSection(BaseModel):
    """SuperAgent 后台子 Agent（全异步 task + HITL 审批续跑）。"""

    max_concurrent_per_session: int = Field(default=3, ge=1)
    task_timeout_seconds: float = Field(default=900, gt=0)
    # 前台等待上限：超过即自动转后台（同步转异步）
    foreground_max_wait_seconds: float = Field(default=120, gt=0)
    # 后台任务终态后自动续跑主 Agent（无活跃 run 时创建 continuation run）
    auto_continue: bool = Field(default=True)
    # 后台命令任务（execute run_in_background）超时：0=不限时
    shell_task_timeout_seconds: float = Field(default=0, ge=0)


class MessagingYamlSection(BaseModel):
    """多通道运行时（与 settings 配置面分离）。"""

    telegram_runtime_enabled: bool = False
    telegram_poll_timeout_seconds: int = Field(default=25, ge=1, le=60)
    feishu_runtime_enabled: bool = False


class SettingsFeaturesYamlSection(BaseModel):
    """设置控制面分域开关；用于分阶段交付与安全回滚。"""

    provider_models: bool = False
    mcp_management: bool = False
    automation_operations: bool = False
    channel_operations: bool = False
    agent_context: bool = False
    observability: bool = False
    import_export: bool = False


class ChatAttachmentYamlSection(BaseModel):
    enabled: bool = True
    ttl_days: int = Field(default=7, ge=1)
    max_file_mb: int = Field(default=20, ge=1)
    auto_convert: bool = True
    max_image_mb: int = Field(default=5, ge=1)
    vision_enabled: bool = True
    reinject_session_images: bool = True
    max_files_per_message: int = Field(default=10, ge=1)
    image_inject_max_edge: int = Field(default=1536, ge=256, le=4096)
    vlm_fallback_enabled: bool = True
    tiny_inline_chars: int = Field(default=4096, ge=0)
    read_page_lines: int = Field(default=2000, ge=1)
    preview_chars: int = Field(default=500, ge=1)


class KbDeepDocYamlSection(BaseModel):
    enabled: bool = True
    model_dir: str = "../.noesis/rag/res/deepdoc"


class KbParserYamlSection(BaseModel):
    default: str = "deepdoc"


class KbYamlSection(BaseModel):
    deepdoc: KbDeepDocYamlSection = Field(default_factory=KbDeepDocYamlSection)
    parser: KbParserYamlSection = Field(default_factory=KbParserYamlSection)


class MemoryYamlSection(BaseModel):
    """md 文件记忆层（openspec: md-memory-layer）。"""
    extraction_model: str = ""  # 空 = 默认对话模型
    selection_model: str = ""  # 空 = 默认对话模型（注入选条）
    enabled_by_default: bool = False  # fail-closed：抽取评测门禁未过不默认开启
    session_idle_minutes: int = Field(default=10, ge=1, le=120)
    sweep_interval_minutes: int = Field(default=30, ge=5, le=240)
    max_entries_per_extraction: int = Field(default=3, ge=1, le=10)
    index_max_lines: int = Field(default=200, ge=50, le=1000)
    index_max_bytes: int = Field(default=25_600, ge=10_000, le=100_000)
    stale_warning_days: int = Field(default=2, ge=1, le=90)
    inject_budget_tokens: int = Field(default=2000, ge=500, le=10_000)
    max_entry_chars: int = Field(default=4000, ge=500, le=20_000)
    consolidation_min_interval_hours: int = Field(default=24, ge=1, le=24 * 30)
    consolidation_min_new_sessions: int = Field(default=5, ge=1, le=200)
    max_message_chars: int = Field(default=120_000, ge=10_000, le=1_000_000)


class AppYamlConfig(BaseModel):
    config_version: int = 1
    app: AppYamlSection = Field(default_factory=AppYamlSection)
    session: SessionYamlSection = Field(default_factory=SessionYamlSection)
    database: DatabaseYamlSection = Field(default_factory=DatabaseYamlSection)
    model: ModelYamlSection = Field(default_factory=ModelYamlSection)
    embedding: EmbeddingYamlSection = Field(default_factory=EmbeddingYamlSection)
    rerank: RerankYamlSection = Field(default_factory=RerankYamlSection)
    vlm: VlmYamlSection = Field(default_factory=VlmYamlSection)
    context: ContextYamlSection = Field(default_factory=ContextYamlSection)
    summarization: SummarizationYamlSection = Field(
        default_factory=SummarizationYamlSection
    )
    agent_runtime: AgentRuntimeYamlSection = Field(
        default_factory=AgentRuntimeYamlSection
    )
    retrieval_limits: RetrievalLimitsYamlSection = Field(
        default_factory=RetrievalLimitsYamlSection
    )
    stream: StreamYamlSection = Field(default_factory=StreamYamlSection)
    qdrant: QdrantYamlSection = Field(default_factory=QdrantYamlSection)
    langfuse: LangfuseYamlSection = Field(default_factory=LangfuseYamlSection)
    other: OtherYamlSection = Field(default_factory=OtherYamlSection)
    skills_market: SkillsMarketYamlSection = Field(
        default_factory=SkillsMarketYamlSection
    )
    web_tools: WebToolsYamlSection = Field(default_factory=WebToolsYamlSection)
    checkpoint: CheckpointYamlSection = Field(default_factory=CheckpointYamlSection)
    chat_attachment: ChatAttachmentYamlSection = Field(
        default_factory=ChatAttachmentYamlSection
    )
    sandbox: SandboxYamlSection = Field(default_factory=SandboxYamlSection)
    hitl: HitlYamlSection = Field(default_factory=HitlYamlSection)
    subagents: SubagentsYamlSection = Field(default_factory=SubagentsYamlSection)
    messaging: MessagingYamlSection = Field(default_factory=MessagingYamlSection)
    settings_features: SettingsFeaturesYamlSection = Field(
        default_factory=SettingsFeaturesYamlSection
    )
    kb: KbYamlSection = Field(default_factory=KbYamlSection)
    memory: MemoryYamlSection = Field(default_factory=MemoryYamlSection)


@lru_cache
def load_app_yaml() -> AppYamlConfig:
    path = resolve_config_path()
    if not path.is_file():
        logger.warning(
            "未找到 config.yaml（{}），使用内置默认；可复制 config.example.yaml",
            path,
        )
        return AppYamlConfig()

    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    resolved = resolve_env_variables(raw)
    return AppYamlConfig.model_validate(resolved)
