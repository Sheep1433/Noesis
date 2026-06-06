## Purpose

本能力定义 Noesis **知识库检索模块**（`kb-retrieval`）的对外行为：在单一门面下提供向量、BM25 与混合（RRF）检索，供 HTTP API 与 Agent RAG 复用；存储与查询均以 Qdrant 为主，BM25 首版为进程内索引并文档化多实例限制。

## ADDED Requirements

### Requirement: 统一检索门面

系统 SHALL 提供知识库检索门面（例如 `KbRetrievalService`），接受 `collection_name`、`query` 文本及 `search_mode`（`vector` | `bm25` | `hybrid`），返回统一结构的命中列表（分片 id、score、content 摘要、file_name 及可选 rank 元数据）。

#### Scenario: 向量检索成功

- **WHEN** 调用方指定 `search_mode=vector` 且集合存在、Qdrant 已连接、查询文本非空
- **THEN** 系统 SHALL 返回按向量相似度排序的结果列表，且每条结果 SHALL 包含可用于追溯的 `id` 与 `score`

#### Scenario: 集合不存在

- **WHEN** 调用方对不存在的 `collection_name` 发起任意模式的检索
- **THEN** 系统 SHALL 返回 HTTP 404（或业务码与之一致）及统一失败结构，且 SHALL NOT 返回空列表冒充成功

### Requirement: BM25 检索模式

系统 SHALL 在 `search_mode=bm25` 时使用关键词检索策略返回 top-k 分片；当 BM25 索引尚未构建时 SHALL 尝试从该集合的 Qdrant points 加载文档并构建索引，若集合无文档则返回空列表并记录可观测日志。中文查询 SHALL 经 jieba 分词预处理。

#### Scenario: BM25 检索有命中

- **WHEN** 集合内已有已索引分片且查询词与文档内容相关
- **THEN** 系统 SHALL 返回基于 BM25 分数排序的结果，且响应中 SHALL 标明检索模式为 `bm25`

### Requirement: 混合检索模式（RRF）

系统 SHALL 在 `search_mode=hybrid` 时使用 **RRF**（Reciprocal Rank Fusion）融合向量与 BM25 两路排序，分数为 `Σ 1/(rrf_k + rank + 1)`；`rrf_k` 默认 60，支持请求级覆盖。

#### Scenario: 混合检索默认 RRF

- **WHEN** 调用方指定 `search_mode=hybrid` 且未提供 `rrf_k`
- **THEN** 系统 SHALL 使用 `rrf_k=60` 完成融合，且行为与模块内文档一致

### Requirement: 检索执行参数合并

检索门面 SHALL 使用平台代码默认（`limit`、`score_threshold` 等）并与单次请求显式字段合并；显式请求字段 SHALL 优先于代码默认。系统 SHALL NOT 从 MySQL 读取 per-collection 默认。

#### Scenario: 未传 limit 使用平台默认

- **WHEN** HTTP 请求未设置 `limit`
- **THEN** 实际条数上限 SHALL 为平台默认值（当前 10）

### Requirement: HTTP 检索 API 暴露三种模式

系统 SHALL 通过 `POST /api/knowledge_base/collections/{collection_name}/search` 接受可选字段 `search_mode`；缺省时 SHALL 等价于 `vector`；成功时 SHALL 返回 `ResponseUtil.success(data=...)`。

#### Scenario: 旧客户端不传 search_mode

- **WHEN** 请求体仅包含 `query`（及既有可选字段）
- **THEN** 系统 SHALL 按向量检索执行且 SHALL NOT 因缺少 `search_mode` 返回 4xx

#### Scenario: Qdrant 不可用

- **WHEN** 向量服务未连接且调用方请求 `vector` 或 `hybrid`
- **THEN** 系统 SHALL 返回 HTTP 503 及明确错误信息

### Requirement: Agent 经门面调用

通用问答、测试用例生成等 Agent 的 RAG 召回路径 SHALL 通过检索门面获取上下文，而 SHALL NOT 在 Agent 模块内直接调用底层 Qdrant 客户端完成检索（入库与管理操作除外）。

#### Scenario: RAG 召回与 API 一致

- **WHEN** 同一 `collection_name` 与 `query` 经 API 与 Agent 门面各调用一次且参数相同
- **THEN** 两者 SHALL 返回语义一致的 top-k 结果（允许因并发写入产生的顺序微差）

### Requirement: 元数据过滤

检索门面 SHALL 接受可选 `filters` 字典，在指定 `collection_name` 范围内缩小命中范围。首版 SHALL 支持：`file_name` 或 `source_name` 精确匹配；`Header_1` 至 `Header_4` 精确匹配（多键同时为 AND）；`header_path_prefix` 对 `header_path` 前缀匹配。过滤 SHALL 与 `search_mode` 组合使用。

#### Scenario: 按文档名过滤

- **WHEN** 调用方传入 `filters={"file_name": "spec.docx"}` 且集合内存在该文档分片
- **THEN** 返回结果 SHALL 仅包含 payload 中 `file_name` 或 `source_name` 与该值一致的分片

#### Scenario: 按标题层级精确过滤

- **WHEN** 调用方传入 `filters={"Header_2": "技术规格"}` 且存在入库时写入 `Header_2=技术规格` 的分片
- **THEN** 返回结果 SHALL 仅包含满足该字段精确匹配的分片

#### Scenario: 按标题路径前缀过滤

- **WHEN** 调用方传入 `filters={"header_path_prefix": "spec.docx > 第一章"}` 且分片 `header_path` 以该前缀开头
- **THEN** 该分片 SHALL 出现在结果中；不以该前缀开头的分片 SHALL NOT 出现

#### Scenario: 过滤无匹配

- **WHEN** `filters` 与集合内所有分片均不匹配
- **THEN** 系统 SHALL 返回空列表及 HTTP 200（或项目统一成功结构），且 SHALL NOT 返回 500

### Requirement: 入库标题 metadata 与过滤一致性

文档上传入库 SHALL 一律按 Markdown 标题分块，将 `Header_1`~`Header_4`、`header_path`、`source_name` 等写入 Qdrant point payload。检索过滤 SHALL NOT 假设历史滑窗分片具备层级信息。

#### Scenario: 上传后可按 Header 过滤

- **WHEN** 客户端上传含 `## 技术规格` 章节的 Markdown，随后检索并传入 `filters={"Header_2": "技术规格"}`
- **THEN** 系统 SHALL 至少返回一个来自该章节的分片
