## Purpose

本能力描述 Noesis **知识库 HTTP API**（前缀 `/api/knowledge_base`）：Qdrant 连接状态、集合 CRUD、文档与分片查询、上传入库与检索。向量与分片数据 **以 Qdrant 为唯一数据源**；集合级 `processing_params` / `query_params` **以 MySQL `kb_collection_config` 为权威配置**，经 `merge_query_execution_params` / `resolve_effective_processing_params` 与单次请求覆盖合并。

**模块委托**（行为细则不在本 spec 重复）：

- 解析 → `kb-document-parse`
- 分块 → `kb-chunking`
- 检索 → `kb-retrieval`
- 单集合离线评测 → `kb-evaluation`

## Requirements

### Requirement: 向量服务可观测性

系统 SHALL 提供 `GET /api/knowledge_base/status`，返回 Qdrant 是否连通及集合数量等摘要信息。

#### Scenario: Qdrant 不可达

- **WHEN** 向量服务连接失败或超时
- **THEN** 接口 SHALL 仍返回结构化状态且 `connected` 为 false，不得因未捕获异常导致 500 无信息体

### Requirement: 集合管理

系统 SHALL 支持列出、创建、按名称查询详情与删除集合；操作须与 Qdrant 实际状态一致；资源不存在时返回 404 或业务码与 HTTP 一致。

#### Scenario: 创建重名集合

- **WHEN** 客户端创建已存在的 collection 名称
- **THEN** 系统 SHALL 返回 409，且不在 Qdrant 产生重复主键冲突

### Requirement: 集合配置与 Qdrant 生命周期同步

创建集合时 SHALL 插入 `kb_collection_config` 默认行（`processing_params` / `query_params` 为平台默认）；删除集合时 SHALL 删除对应配置行。

#### Scenario: 创建集合产生配置行

- **WHEN** 客户端成功创建 collection
- **THEN** MySQL SHALL 存在同名配置记录

### Requirement: 集合配置 API

系统 SHALL 提供 `GET /api/knowledge_base/collections/{collection_name}/config` 返回 `processing_params` 与 `query_params`。系统 SHALL 提供 `PATCH` 同路径部分更新（JSON 字段 deep-merge）。集合在 Qdrant 或 MySQL 不存在时 SHALL 返回 404。

#### Scenario: 读取集合配置

- **WHEN** 集合在 Qdrant 与 MySQL 均存在
- **THEN** GET SHALL 返回 200 及完整配置 JSON

#### Scenario: 更新检索默认

- **WHEN** PATCH 提交 `query_params.use_reranker=false`
- **THEN** 后续检索 SHALL 禁用 rerank（直至再次更新）

### Requirement: 文档与分片

系统 SHALL 提供集合下文档列表、文档删除、分片列表与分片详情接口；返回模型须与 `schemas.knowledge_base_schema` 一致。

#### Scenario: 访问不存在的文档

- **WHEN** 请求的路径指向不存在于该集合的文档或分片
- **THEN** 系统 SHALL 返回 404 及统一失败结构

### Requirement: 检索与上传流水线

系统 SHALL 提供检索与文档上传解析流水线；检索 **SHALL 委托 `KbRetrievalService`**（见 `kb-retrieval`）；上传 **SHALL 委托 `ParserFactory` + `chunk_text_for_kb`**（见 `kb-document-parse`、`kb-chunking`）。大文件须受大小与超时约束。检索成功响应 SHALL 使用 `ResponseUtil.success` 包装 `data` 列表。

#### Scenario: 上传成功

- **WHEN** 客户端上传允许格式且未超限制的文件到指定集合
- **THEN** 系统 SHALL 返回成功结构并在集合中可检索到新文档或分片

### Requirement: 检索执行默认参数

公开检索接口在调用方未显式指定检索字段时 SHALL 使用 **MySQL `kb_collection_config.query_params`** 与平台默认值合并后的结果。平台默认 SHALL 为：`search_mode=hybrid`、`use_reranker=true`、`recall_top_k=50`、`final_top_k=10`、`score_threshold=null`、`rrf_k=60`。显式请求字段 SHALL 优先于集合默认与平台默认。

#### Scenario: 未传 final_top_k 时使用集合默认

- **WHEN** 集合 `query_params.final_top_k=15` 且客户端未传 `final_top_k` 或 deprecated `limit`
- **THEN** 实际返回条数上限 SHALL 为 15

#### Scenario: 新集合使用平台 hybrid 默认

- **WHEN** 创建新集合且未自定义 `query_params`
- **THEN** 检索时缺省 `search_mode` SHALL 等价于 `hybrid`

### Requirement: 检索请求体字段

`POST /api/knowledge_base/collections/{collection_name}/search` 请求体 SHALL 支持：

- 必填：`query`
- 可选：`search_mode`、`use_reranker`、`recall_top_k`、`final_top_k`、`score_threshold`、`rrf_k`、`filters`
- deprecated 别名：`limit`（等价 `final_top_k`；与 `final_top_k` 同时传时 **`final_top_k` 优先**）

响应命中项 SHALL 含 `search_mode`；启用 rerank 时 MAY 含 `rerank_score`、`recall_score`。过滤语义 SHALL 与 `kb-retrieval` 一致。

#### Scenario: 显式关闭 rerank

- **WHEN** 请求体 `use_reranker=false`
- **THEN** 响应 SHALL 仅基于 recall 分数排序

#### Scenario: limit 别名映射

- **WHEN** 请求体仅传 `limit=8` 且未传 `final_top_k`
- **THEN** 实际 `final_top_k` SHALL 为 8

### Requirement: 请求体向后兼容

对既有上传与检索端点，旧客户端在不携带新字段时 SHALL **保持 HTTP 成功路径**（不因缺少可选字段返回 4xx，除非原有校验本就失败）。**注意**：缺省 `search_mode` 由历史 `vector` 变更为 **`hybrid`**，排序结果可能变化，属预期精度提升，**不**视为请求体 breaking change。

缺省 `parser_id` SHALL 等价于 `deepdoc`。缺省 `chunk_template_id` SHALL 等价于 `general`。

#### Scenario: 旧客户端无新字段仍可上传

- **WHEN** 客户端使用最小必填字段调用上传接口
- **THEN** 系统 SHALL 使用 DeepDoc 默认解析与 `general` 分块模板完成处理

#### Scenario: 旧客户端无 search_mode 走 hybrid

- **WHEN** 检索请求体仅含 `query`
- **THEN** 系统 SHALL 执行 hybrid 检索且 SHALL NOT 返回 4xx

### Requirement: 上传 processing_params

上传 API SHALL 接受可选 `processing_params`（含 `parser_id`、`chunk_template_id`、`chunk_parser_config`）；未传时使用集合 MySQL 默认。

#### Scenario: 当次覆盖 chunk_size 不写回集合默认

- **WHEN** 当次上传传入 `chunk_parser_config.chunk_size=800` 且未 PATCH 集合配置
- **THEN** 该次入库 SHALL 使用 800
- **AND** 集合 MySQL 默认 SHALL 保持不变

### Requirement: 检索与入库配置一致性校验

系统在入库索引前 SHALL 校验嵌入向量维度与目标 collection 向量维度一致；不一致时 SHALL 返回 **409** 及可行动错误信息，且 SHALL NOT 写入不一致维度的点。

#### Scenario: 嵌入维度与 collection 不匹配

- **WHEN** 文档嵌入维度与集合 vector 维度不一致
- **THEN** 系统 SHALL 拒绝该次上传并返回 HTTP 409

### Requirement: 分片 payload 可追溯

对本 change 落地后新索引的 Qdrant 点，payload SHALL 含 `effective_processing_params` 快照（见 `kb-chunking`）。旧分片 MAY 无该字段，SHALL NOT 因此被拒绝检索。

#### Scenario: 分片详情含生效参数

- **WHEN** 管理员查看本 change 落地后入库的分片详情
- **THEN** 响应 SHALL 包含 `effective_processing_params` 或等价字段

### Requirement: Qdrant 不可用时的检索

当 Qdrant 未连接且请求 `search_mode` 为 `vector` 或 `hybrid` 时，检索 API SHALL 返回 HTTP 503 及明确错误信息（`bm25`-only 路径若实现则文档化，首版与 `kb-retrieval` 一致为 503）。

#### Scenario: 向量库断开

- **WHEN** `is_qdrant_connected()` 为 false
- **THEN** 检索 API SHALL 返回 503
