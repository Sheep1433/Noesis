from pydantic import BaseModel, Field


class ModelCatalogItem(BaseModel):
    id: str = Field(..., description="模型目录 id，前端选择与请求 extra.model_id 使用")
    label: str = Field(..., description="展示名称")
    model_name: str = Field(..., description="上游模型名")
    model_type: str = Field(..., description="模型 provider 类型")
    is_default: bool = Field(False, description="是否为默认模型")
    supports_vision: bool = Field(False, description="是否支持原生 multimodal 看图")


class ModelCatalogResponse(BaseModel):
    models: list[ModelCatalogItem]
    default_id: str
    first_vision_model_id: str | None = Field(
        None, description="catalog 中首个支持 Vision 的 model id，供上传图片时自动切换"
    )
    vlm_fallback_available: bool = Field(
        False, description="主模型非 Vision 时是否可用独立 VLM 生成图片描述"
    )
