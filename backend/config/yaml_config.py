"""非敏感运行时配置：从 config.yaml 加载。"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from common.logging import logger


def _backend_dir() -> Path:
    return Path(__file__).resolve().parent.parent


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


class JwtYamlSection(BaseModel):
    algorithm: str = "HS256"
    expire_minutes: int = 1440
    redis_expire_minutes: int = 30
    stop_token_expire_minutes: int = 15


class DatabaseYamlSection(BaseModel):
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
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


class ModelYamlSection(BaseModel):
    """主对话 LLM：type / name / base_url；api_key 在 .env MODEL_API_KEY。"""

    type: str = "qwen"
    name: str = "qwen-plus"
    temperature: float = 0.75
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    show_thinking_process: bool = True
    request_timeout: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=2, ge=0)
    generation: ModelGenerationYamlSection = Field(default_factory=ModelGenerationYamlSection)


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
    max_input_tokens: int = Field(default=0, ge=0)
    display_enabled: bool = True


class SummarizationYamlSection(BaseModel):
    enabled: bool = True
    # 仅模型名单独配置；type / base_url / api_key 与 model 层一致
    model_name: str = ""
    temperature: float = 0.0
    # trigger_tokens > 0 时优先；为 0 时用 trigger_fraction × context.max_input_tokens
    trigger_tokens: int = Field(default=96000, ge=0)
    trigger_fraction: float = Field(default=0.75, gt=0, le=1)
    max_input_tokens: int = Field(default=0, ge=0)
    tool_offload_threshold: int = Field(default=6000, ge=1)
    max_retention_ratio: float = Field(default=0.65, gt=0, le=1)
    messages_to_keep: int = Field(default=28, ge=1)


class LoopDetectionYamlSection(BaseModel):
    enabled: bool = True
    warn_threshold: int = Field(default=3, ge=1)
    hard_limit: int = Field(default=5, ge=2)
    window_size: int = Field(default=20, ge=1)
    max_tracked_threads: int = Field(default=100, ge=1)
    tool_freq_warn: int = Field(default=30, ge=1)
    tool_freq_hard_limit: int = Field(default=50, ge=1)


class ToolCallLimitYamlSection(BaseModel):
    enabled: bool = True
    thread_limit: int | None = Field(default=200, ge=1)
    run_limit: int | None = Field(default=None, ge=1)
    exit_behavior: str = "continue"
    task_run_limit: int | None = Field(default=10, ge=1)


class AgentRuntimeYamlSection(BaseModel):
    dangling_tool_call_repair_enabled: bool = True
    tool_call_limit: ToolCallLimitYamlSection = Field(default_factory=ToolCallLimitYamlSection)


class StreamYamlSection(BaseModel):
    sse_keepalive_interval_seconds: float = Field(default=25.0, ge=0)


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


class WebToolsYamlSection(BaseModel):
    max_search_results: int = Field(default=8, ge=1, le=20)
    fetch_max_chars: int = Field(default=4096, ge=1)
    fetch_timeout_seconds: int = Field(default=30, ge=1)


class CheckpointYamlSection(BaseModel):
    db_path: str = "../.data/checkpoints/langgraph_checkpoints.sqlite"


class SandboxYamlSection(BaseModel):
    # aio：经 sandbox-runner 起 AIO 容器（生产推荐）；local_shell：宿主机进程内执行
    backend: str = "aio"
    runner_url: str = "http://127.0.0.1:8090"
    execute_timeout_seconds: int = Field(default=120, ge=1)


class ChatAttachmentYamlSection(BaseModel):
    enabled: bool = True
    dir: str = "../.data/chat_attachments"
    ttl_days: int = Field(default=7, ge=1)
    max_file_mb: int = Field(default=20, ge=1)
    max_count_per_session: int = Field(default=10, ge=1)
    auto_convert: bool = True
    max_image_mb: int = Field(default=5, ge=1)
    vision_enabled: bool = True
    reinject_session_images: bool = True
    max_images_per_message: int = Field(default=3, ge=1)
    tiny_inline_chars: int = Field(default=4096, ge=0)
    read_page_lines: int = Field(default=2000, ge=1)
    preview_chars: int = Field(default=500, ge=1)


class KbDeepDocYamlSection(BaseModel):
    enabled: bool = True
    model_dir: str = "../.data/rag/res/deepdoc"


class KbParserYamlSection(BaseModel):
    default: str = "deepdoc"


class KbYamlSection(BaseModel):
    deepdoc: KbDeepDocYamlSection = Field(default_factory=KbDeepDocYamlSection)
    parser: KbParserYamlSection = Field(default_factory=KbParserYamlSection)


class AppYamlConfig(BaseModel):
    config_version: int = 1
    app: AppYamlSection = Field(default_factory=AppYamlSection)
    jwt: JwtYamlSection = Field(default_factory=JwtYamlSection)
    database: DatabaseYamlSection = Field(default_factory=DatabaseYamlSection)
    model: ModelYamlSection = Field(default_factory=ModelYamlSection)
    embedding: EmbeddingYamlSection = Field(default_factory=EmbeddingYamlSection)
    rerank: RerankYamlSection = Field(default_factory=RerankYamlSection)
    vlm: VlmYamlSection = Field(default_factory=VlmYamlSection)
    context: ContextYamlSection = Field(default_factory=ContextYamlSection)
    summarization: SummarizationYamlSection = Field(default_factory=SummarizationYamlSection)
    loop_detection: LoopDetectionYamlSection = Field(default_factory=LoopDetectionYamlSection)
    agent_runtime: AgentRuntimeYamlSection = Field(default_factory=AgentRuntimeYamlSection)
    stream: StreamYamlSection = Field(default_factory=StreamYamlSection)
    qdrant: QdrantYamlSection = Field(default_factory=QdrantYamlSection)
    langfuse: LangfuseYamlSection = Field(default_factory=LangfuseYamlSection)
    other: OtherYamlSection = Field(default_factory=OtherYamlSection)
    web_tools: WebToolsYamlSection = Field(default_factory=WebToolsYamlSection)
    checkpoint: CheckpointYamlSection = Field(default_factory=CheckpointYamlSection)
    chat_attachment: ChatAttachmentYamlSection = Field(default_factory=ChatAttachmentYamlSection)
    sandbox: SandboxYamlSection = Field(default_factory=SandboxYamlSection)
    kb: KbYamlSection = Field(default_factory=KbYamlSection)


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
