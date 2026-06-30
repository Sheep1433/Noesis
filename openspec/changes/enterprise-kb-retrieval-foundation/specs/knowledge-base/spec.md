## MODIFIED Requirements

### Requirement: 检索执行默认参数

公开检索接口在调用方未显式指定检索字段时 SHALL 使用 **集合 MySQL 配置** `kb_collection_config.query_params` 与平台默认值合并后的结果；平台默认 SHALL 为 `search_mode=hybrid`、`use_reranker=true`、`recall_top_k=50`、`final_top_k=10`、`score_threshold=null`。显式请求字段 SHALL 优先于集合默认与平台默认。系统 SHALL NOT 在无配置时回退到「仅代码常量、忽略 MySQL」的旧行为。

#### Scenario: 未传 limit 时使用集合 final_top_k

- **WHEN** 集合 `query_params.final_top_k=15` 且客户端未传 `limit` 或 `final_top_k`
- **THEN** 实际返回条数上限 SHALL 为 15

#### Scenario: 新集合使用平台 hybrid 默认

- **WHEN** 创建新集合且未自定义 `query_params`
- **THEN** 检索时缺省 `search_mode` SHALL 等价于 `hybrid`

### Requirement: API 向后兼容

对 `knowledge-base` 已暴露的上传与检索端点，旧客户端在不携带新字段时 SHALL 保持成功路径。缺省 `search_mode` SHALL 等价于 **hybrid**（行为相对历史「vector 默认」有提升，但请求体形状兼容）。缺省 `chunk_preset_id` SHALL 等价于 `general`。

#### Scenario: 旧客户端无新字段仍可上传

- **WHEN** 客户端使用最小必填字段调用上传接口
- **THEN** 系统 SHALL 使用 `general` preset 与集合默认解析器完成处理
- **AND** SHALL NOT 因缺少可选参数返回 4xx（除非原有校验失败）

#### Scenario: 旧客户端无 search_mode 走 hybrid

- **WHEN** 检索请求体仅含 `query`
- **THEN** 系统 SHALL 执行 hybrid 检索且 SHALL NOT 返回 4xx

### Requirement: 平台固定 Markdown 标题分块

系统 SHALL 对知识库入库使用 **`kb-chunking` 多 preset 调度** 作为唯一分块路径；默认 preset 为 `general`（含 Markdown 标题感知）。`strategy=markdown_headers` 仅作为兼容别名，SHALL 映射为 `general`。默认 `chunk_size` / `chunk_overlap` 仍由平台常量提供，并 MAY 被 `chunk_parser_config` 覆盖。

#### Scenario: 上传 Markdown 文档

- **WHEN** 客户端上传合法 Markdown 且未指定 `chunk_preset_id`
- **THEN** 系统 SHALL 经 `general` preset 产出分片并写入 Qdrant
- **AND** 分片 payload MAY 含 `header_path` 等标题路径元数据

#### Scenario: 标题切分异常时滑窗回退

- **WHEN** preset 分块抛出异常或返回空分片列表
- **THEN** 系统 SHALL 使用滑窗分块完成入库，且 SHALL 记录 warning 日志

### Requirement: processing_params 分层合并（内部）

系统 SHALL 通过 `resolve_effective_processing_params` 按优先级合并：**集合 MySQL 默认** → **文档持久化覆盖** → **仅当次 ingest 的 request_once**。对外上传 API SHALL 接受可选 `processing_params`（含 `chunk_preset_id`、`chunk_parser_config`、`parser_id`）；未传时使用集合默认。

#### Scenario: 仅当次覆盖不写回文档默认

- **WHEN** 当次上传传入 `chunk_preset_id=qa` 且未更新文档持久化配置
- **THEN** 该次入库 SHALL 使用 `qa`
- **AND** 后续同文档再处理 SHALL NOT 默认沿用该临时值

## ADDED Requirements

### Requirement: 集合配置 API

系统 SHALL 提供 `GET /api/knowledge_base/collections/{collection_name}/config` 返回 `processing_params` 与 `query_params`。系统 SHALL 提供 `PATCH` 同路径更新上述 JSON 字段（部分更新合并）。集合不存在时 SHALL 返回 404。

#### Scenario: 读取集合配置

- **WHEN** 集合在 Qdrant 与 MySQL 均存在
- **THEN** GET SHALL 返回 200 及完整配置 JSON

#### Scenario: 更新检索默认

- **WHEN** PATCH 提交 `query_params.use_reranker=false`
- **THEN** 后续检索 SHALL 禁用 rerank（直至再次更新）

### Requirement: 检索请求体扩展

`POST /api/knowledge_base/collections/{collection_name}/search` 请求体 SHALL 支持可选字段：`use_reranker`、`recall_top_k`、`final_top_k`（与 `limit` 互斥时以 `final_top_k` 为准并文档化）。响应命中项 MAY 含 `rerank_score`。

#### Scenario: 显式关闭 rerank

- **WHEN** 请求体 `use_reranker=false`
- **THEN** 响应 SHALL 仅基于 recall 分数排序

### Requirement: 集合配置与 Qdrant 生命周期同步

创建集合时 SHALL 插入 `kb_collection_config` 默认行；删除集合时 SHALL 删除对应配置行。

#### Scenario: 创建集合产生配置行

- **WHEN** 客户端成功创建 collection
- **THEN** MySQL SHALL 存在同名配置记录
