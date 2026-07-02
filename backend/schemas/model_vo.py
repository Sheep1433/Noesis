from pydantic import BaseModel, Field


class ModelCatalogItem(BaseModel):
    id: str = Field(..., description="模型目录 id，前端选择与请求 extra.model_id 使用")
    label: str = Field(..., description="展示名称")
    model_name: str = Field(..., description="上游模型名")
    model_type: str = Field(..., description="模型 provider 类型")
    is_default: bool = Field(False, description="是否为默认模型")


class ModelCatalogResponse(BaseModel):
    models: list[ModelCatalogItem]
    default_id: str
