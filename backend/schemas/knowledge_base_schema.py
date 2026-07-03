"""
知识库相关 Pydantic 模型
"""
from typing import Any, Dict, List, Literal, Optional

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


class CollectionConfigResponse(BaseModel):
    """集合 MySQL 配置"""
    collection_name: str
    processing_params: Dict[str, Any] = Field(default_factory=dict)
    query_params: Dict[str, Any] = Field(default_factory=dict)


class PatchCollectionConfigRequest(BaseModel):
    """PATCH 集合配置（deep-merge）"""
    processing_params: Optional[Dict[str, Any]] = Field(
        None, description="入库参数片段，与现有配置 deep-merge"
    )
    query_params: Optional[Dict[str, Any]] = Field(
        None, description="检索参数片段，与现有配置 deep-merge"
    )


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
    effective_processing_params: Optional[Dict[str, Any]] = Field(
        None, description="入库时生效的处理参数快照"
    )


class SearchResult(BaseModel):
    """检索结果"""
    id: str
    score: float
    content: str
    file_name: str
    search_mode: str = Field(default="hybrid", description="本次使用的检索模式")
    header_path: Optional[str] = Field(None, description="分片标题路径")
    recall_score: Optional[float] = Field(None, description="召回阶段分数")
    rerank_score: Optional[float] = Field(None, description="rerank 分数（启用时）")


class SearchTiming(BaseModel):
    """检索各阶段耗时（毫秒）"""
    prepare_ms: float = Field(..., description="检索实例准备耗时（含首次 BM25 索引加载）")
    recall_ms: float = Field(..., description="召回阶段耗时")
    parse_ms: float = Field(..., description="命中组装耗时")
    rerank_ms: float = Field(..., description="重排序耗时")
    post_ms: float = Field(..., description="阈值过滤与截断耗时")
    total_ms: float = Field(..., description="总耗时")
    rerank_applied: bool = Field(..., description="是否实际执行 rerank")
    recall_hits: int = Field(..., description="召回命中数")
    final_hits: int = Field(..., description="最终返回条数")
    search_mode: str = Field(..., description="本次检索模式")


class SearchCollectionResponse(BaseModel):
    """POST /search 响应 data"""
    results: List[SearchResult]
    timing: SearchTiming


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


class ProcessingParamsBody(BaseModel):
    """当次上传可选 processing_params"""
    chunk_preset_id: Optional[str] = Field(None, description="固定 general")
    parser_id: Optional[str] = Field(None, description="固定 deepdoc")
    chunk_parser_config: Optional[Dict[str, Any]] = Field(
        None, description="chunk_size / chunk_overlap"
    )


class SearchCollectionBody(BaseModel):
    """POST /search 请求体"""
    query: str = Field(..., description='查询文本')
    limit: Optional[int] = Field(
        None, description='deprecated 别名，等价 final_top_k'
    )
    final_top_k: Optional[int] = Field(None, description='最终返回条数上限')
    recall_top_k: Optional[int] = Field(None, description='召回阶段候选上限')
    use_reranker: Optional[bool] = Field(None, description='是否 cross-encoder 精排')
    score_threshold: Optional[float] = Field(
        None, description='rerank 之后的分数过滤阈值'
    )
    search_mode: Optional[Literal["vector", "bm25", "hybrid"]] = Field(
        default=None,
        description="检索模式；缺省使用集合 MySQL 默认（hybrid）",
    )
    filters: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "元数据过滤：file_name/source_name/Header_1~4 精确匹配；"
            "header_path_prefix 为 header_path 前缀；file_name_in 为文件名列表"
        ),
    )
    rrf_k: Optional[int] = Field(
        None,
        ge=1,
        le=500,
        description="混合检索 RRF 平滑常数（默认 60）",
    )
