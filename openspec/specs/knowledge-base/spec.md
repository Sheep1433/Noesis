## Purpose

本能力描述基于 **Qdrant** 的知识库 HTTP API：连接状态、集合（collection）的增删查、文档与分片（shard）的列表与详情、检索与文件上传入库等行为，供管理端与 RAG 链路对齐验收标准。集合、文档、向量与检索 **均以 Qdrant 为唯一数据源**；入库分块为平台固定 Markdown 标题策略；检索默认 `limit` / `score_threshold` 由代码常量提供，请求体可覆盖。

## Requirements

### Requirement: 向量服务可观测性

系统 SHALL 提供 `GET /api/knowledge_base/status`，返回 Qdrant 是否连通及集合数量等摘要信息，供前端健康展示与运维探测。

#### Scenario: Qdrant 不可达

- **WHEN** 向量服务连接失败或超时
- **THEN** 接口 SHALL 仍返回结构化状态且 `connected` 为 false，不得因未捕获异常导致 500 无信息体（除非配置为严格错误策略且文档化）

### Requirement: 集合管理

系统 SHALL 支持列出、创建、按名称查询详情与删除集合；删除与创建操作须与 Qdrant 实际状态一致，并对资源不存在返回 404 或业务码与 HTTP 状态一致。

#### Scenario: 创建重名集合

- **WHEN** 客户端创建已存在的 collection 名称
- **THEN** 系统 SHALL 返回 409 或项目约定的冲突响应，且不在 Qdrant 产生重复主键冲突

### Requirement: 文档与分片

系统 SHALL 提供集合下文档列表、文档删除、分片列表与分片详情接口，路径与 `collection_name` 参数绑定；返回模型须与 `schemas.knowledge_base_schema` 一致。

#### Scenario: 访问不存在的文档

- **WHEN** 请求的路径指向不存在于该集合的文档或分片
- **THEN** 系统 SHALL 返回 404 及统一失败结构

### Requirement: 检索与上传

系统 SHALL 提供向量 / BM25 / 混合（RRF）检索与文档上传解析流水线，将文件内容写入 Qdrant 并与集合向量维度一致；大文件须受大小与超时约束。检索成功响应 SHALL 使用 `ResponseUtil.success` 包装 `data` 列表。

#### Scenario: 上传成功

- **WHEN** 客户端上传允许格式且未超限制的文件到指定集合
- **THEN** 系统 SHALL 返回成功结构并在集合中可检索到新文档或分片

### Requirement: 检索执行默认参数

公开检索接口在调用方未显式指定 `limit` 或 `score_threshold` 时 SHALL 使用平台代码默认值（当前为 `limit=10`、`score_threshold=null`）；显式请求字段 SHALL 优先于默认值。系统 SHALL NOT 从 MySQL 或其它 OLTP 表读取集合级检索配置。

#### Scenario: 未传 limit 时使用平台默认

- **WHEN** 客户端调用检索接口且未在请求体中设置 `limit`
- **THEN** 实际检索条数上限 SHALL 为平台默认值（10），且行为与改造前未配置 per-collection 默认时一致

### Requirement: API 向后兼容

对 `knowledge-base` 已暴露的上传与检索端点，旧客户端在不携带已移除的 `processing_params` / 集合 `query_params` 字段时 SHALL 保持成功路径；缺省 `search_mode` SHALL 等价于 `vector`。

#### Scenario: 旧客户端无新字段仍可上传

- **WHEN** 客户端使用最小必填字段调用上传接口
- **THEN** 系统 SHALL 按固定 Markdown 标题分块完成处理，且不 SHALL 因缺少可选参数而返回 4xx（除非原有校验本就失败）

### Requirement: 检索与入库配置一致性校验

系统在入库索引前 SHALL 校验嵌入向量维度与目标 collection 向量维度一致。若检测到嵌入维度与 Qdrant collection 配置不一致，SHALL 返回 **409** 及可行动错误信息，且 SHALL 不与「HTTP 与业务码一致」的项目规范相冲突。

#### Scenario: 嵌入维度与 collection 不匹配

- **WHEN** 文档经嵌入模型得到的向量维度与集合创建时配置的 vector 维度不一致
- **THEN** 系统 SHALL 拒绝该次上传并返回 HTTP 409（或业务码与之一致），且 SHALL NOT 写入不一致维度的点

### Requirement: 平台固定 Markdown 标题分块

系统 SHALL 对知识库入库使用 **`strategy=markdown_headers`** 作为唯一生效策略：按 Markdown 标题切分，对超长段二次切分；切分失败或空结果时 **SHALL** 回退内置滑窗分块（`_fixed_window_chunks`）并完成写入，且 **SHALL** 记录 warning 级日志。

默认 `chunk_size` / `chunk_overlap` **SHALL** 为平台常量（当前 `500` / `50`，经 `DEFAULT_COLLECTION_PROCESSING` 与 `fixed_processing_params()` 提供）。`resolve_effective_processing_params` 合并后 **SHALL** 强制 `strategy=markdown_headers`，不得因调用方传入其它 strategy 而改变语义。

#### Scenario: 上传 Markdown 文档

- **WHEN** 客户端向集合上传合法 Markdown 正文且未携带已移除的 `processing_params`
- **THEN** 系统 SHALL 经 `markdown_headers` 路径产出分片并写入 Qdrant，分片 payload **MAY** 含 `header_path` 等标题路径元数据

#### Scenario: 标题切分异常时滑窗回退

- **WHEN** `markdown_headers` 路径抛出异常或返回空分片列表
- **THEN** 系统 SHALL 使用滑窗分块完成入库，且 SHALL 记录可检索的 warning 日志

### Requirement: processing_params 分层合并（内部）

系统 **MAY** 在 Service 层通过 `resolve_effective_processing_params` 按优先级合并入库参数：**集合默认** → **文档持久化覆盖** → **仅当次 ingest 的 request_once**；同一键名上高层 **SHALL** 覆盖低层。仅当次覆盖 **SHALL NOT** 写回文档级持久化默认值。

当前对外上传 API **SHALL** 使用 `fixed_processing_params()`，不接收客户端 `processing_params`；分层合并保留为扩展点，与「API 向后兼容」一致。

#### Scenario: 仅当次覆盖不写回文档默认

- **WHEN** 未来实现支持 `request_once` 覆盖且未更新文档持久化配置
- **THEN** 该次入库 SHALL 使用合并后的临时参数，后续同文档再处理 **SHALL NOT** 默认沿用该临时值

### Requirement: 生效参数可追溯

对本能力落地后新索引的 Qdrant 点，系统 **SHALL** 在 payload 中保留合并后的 **`effective_processing_params`** 快照；旧数据可无该字段，**SHALL NOT** 因此被拒绝检索。

#### Scenario: 分片详情含生效参数

- **WHEN** 管理员通过分片详情接口查看本能力落地后入库的分片
- **THEN** 响应 **SHALL** 包含 `effective_processing_params` 或等价字段；旧数据下该字段 **MAY** 为空

### Requirement: 分块单一入口

系统 SHALL 提供单一文档分块入口（`kb.chunk.chunk` 或 `chunk_text_for_kb` 等价物），以「解析后文本 + effective_params」为输入、分片字符串列表为输出；未知 `strategy` 键 **SHALL** 在合并阶段被强制为 `markdown_headers`，**SHALL NOT** 静默采用与配置不一致的其它策略。

#### Scenario: 入库流水线调用分块入口

- **WHEN** `qdrant_service` 或等价服务执行文档入库
- **THEN** SHALL 经 `chunk(parsed, effective_params=...)` 产出分片列表，且 `effective_params["strategy"]` 为 `markdown_headers`
