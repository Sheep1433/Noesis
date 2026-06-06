"""
知识库相关 Pydantic 模型
"""
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


class CollectionInfo(BaseModel):
    """Collection 信息"""
    name: str
    vector_dimension: int
    documents_count: int = 0
    points_count: int
    created_at: Optional[str] = None


class CollectionDetail(BaseModel):
    """Collection 详细信息（数据来自 Qdrant）"""
    name: str
    vector_dimension: int
    documents_count: int = 0
    points_count: int
    created_at: Optional[str] = None
    status: Optional[str] = None


class DocumentInfo(BaseModel):
    """文档信息"""
    file_name: str
    file_hash: Optional[str] = None
    shard_count: int
    uploaded_at: Optional[str] = None


class ShardInfo(BaseModel):
    """分片信息"""
    id: str
    content: str
    char_length: int
    created_at: Optional[str] = None
    header_path: Optional[str] = Field(None, description="标题路径")
    chunk_index: Optional[int] = Field(None, description="文档内分片序号")


class ShardDetail(BaseModel):
    """分片详情"""
    id: str
    content: str
    char_length: int
    vector_dimension: int
    created_at: Optional[str] = None
    header_path: Optional[str] = Field(None, description="标题路径")
    Header_1: Optional[str] = Field(None, description="一级标题")
    Header_2: Optional[str] = Field(None, description="二级标题")
    Header_3: Optional[str] = Field(None, description="三级标题")
    chunk_index: Optional[int] = Field(None, description="文档内分片序号")
class SearchResult(BaseModel):
    """检索结果"""
    id: str
    score: float
    content: str
    file_name: str
    search_mode: str = Field(default="vector", description="本次使用的检索模式")
    header_path: Optional[str] = Field(None, description="分片标题路径（markdown_headers 入库时有值）")


class KnowledgeBaseStatus(BaseModel):
    """知识库连接状态"""
    connected: bool
    host: str
    port: int
    collections_count: int = 0


class UploadResponse(BaseModel):
    """上传响应"""
    success: bool
    message: str
    file_name: Optional[str] = None
    shards_created: int = 0
    extracted_markdown: Optional[str] = Field(
        default=None,
        description='MarkItDown 解析后的全文 Markdown，供测试用例等流程直接使用',
    )


class DeleteResponse(BaseModel):
    """删除响应"""
    success: bool
    message: str
    deleted_count: int = 0


class CreateCollectionRequest(BaseModel):
    """创建 Collection 请求"""
    name: str = Field(..., description='Collection 名称')
    vector_dimension: int = Field(default=1024, description='向量维度')
    description: Optional[str] = Field(None, description='Collection 描述（当前仅创建请求透传，不落库）')


class CreateCollectionResponse(BaseModel):
    """创建 Collection 响应"""
    success: bool
    message: str
    name: str


class SearchCollectionBody(BaseModel):
    """POST /search 请求体"""
    query: str = Field(..., description='查询文本')
    limit: Optional[int] = Field(None, description='单次检索条数上限，不传则用集合默认值')
    score_threshold: Optional[float] = Field(
        None, description='向量相似度阈值；不传则用集合默认值；显式清除需后续扩展'
    )
    search_mode: Literal["vector", "bm25", "hybrid"] = Field(
        default="vector",
        description="检索模式：vector | bm25 | hybrid",
    )
    filters: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "元数据过滤：file_name/source_name/Header_1~4 精确匹配；"
            "header_path_prefix 为 header_path 前缀"
        ),
    )
    rrf_k: Optional[int] = Field(
        None,
        ge=1,
        le=500,
        description="混合检索 RRF 平滑常数（默认 60）；score=Σ1/(rrf_k+rank+1)",
    )
