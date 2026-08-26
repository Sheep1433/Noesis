from pydantic import BaseModel, Field


class ModelCatalogItem(BaseModel):
    id: str = Field(..., description="模型目录 id，即 provider 模型全名（如 deepseek-v4-flash-free），前端选择与请求 extra.model_id 使用")
    label: str = Field(..., description="展示名称")
    provider: str = Field("", description="所属 provider 标签（按 provider 分组展示用）")
    model_type: str = Field(..., description="模型 provider 类型")
    context_window: int = Field(0, description="上下文窗口上限（token），圆环分母 / 压缩阈值")
    is_default: bool = Field(False, description="是否为默认模型")
    supports_vision: bool = Field(False, description="是否支持原生 multimodal 看图")
    custom: bool = Field(False, description="是否为用户自定义模型")


class ProviderPresetItem(BaseModel):
    id: str = Field(..., description="预设标识（同时是 Provider slug 的默认候选）")
    label: str = Field(..., description="展示名称")
    base_url: str = Field(..., description="OpenAI 兼容端点，选中预设时自动填充")
    headers: dict[str, str] = Field(default_factory=dict, description="归因类 header（无敏感凭证）")


class PlatformProviderInfo(BaseModel):
    """内置目录所属的平台 Provider（按 provider 分组展示用）。"""

    id: str = Field(..., description="Provider 标识（= model.type，如 opencode）")
    label: str = Field(..., description="展示名称（优先取 provider_presets 预设名）")
    base_url: str = Field(..., description="平台端点（平台发现用）")


class ModelCatalogResponse(BaseModel):
    platform_provider: PlatformProviderInfo | None = Field(
        None, description="内置目录的平台 Provider 元数据"
    )
    models: list[ModelCatalogItem]
    provider_presets: list[ProviderPresetItem] = Field(
        default_factory=list,
        description="用户自定义 Provider 的平台预设目录（dsh catalog provider 模式）",
    )
    default_id: str
    first_vision_model_id: str | None = Field(
        None, description="catalog 中首个支持 Vision 的 model id，供上传图片时自动切换"
    )
    vlm_fallback_available: bool = Field(
        False, description="主模型非 Vision 时是否可用独立 VLM 生成图片描述"
    )
