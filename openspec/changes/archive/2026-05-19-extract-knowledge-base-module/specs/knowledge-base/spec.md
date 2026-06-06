## Purpose

本增量规格扩展既有 `knowledge-base` 能力：在保持集合/文档/上传等管理 API 不变的前提下，将公开检索端点的行为与新的 `kb-retrieval` 模块对齐，并验收多检索模式。

## ADDED Requirements

### Requirement: 多模式检索请求体

`POST /api/knowledge_base/collections/{collection_name}/search` 的请求体 SHALL 支持可选字段 `search_mode`，取值为 `vector`、`bm25` 或 `hybrid`；未提供时 SHALL 默认为 `vector`。

#### Scenario: 显式混合检索

- **WHEN** 客户端提交 `search_mode=hybrid` 且查询文本非空
- **THEN** 系统 SHALL 返回混合检索融合后的结果列表，且 SHALL NOT 仅执行纯向量检索

### Requirement: 检索结果可区分模式

检索接口的响应模型 SHALL 允许（或等价字段）标识本次使用的 `search_mode`，以便前端调试与自动化测试断言。

#### Scenario: 响应含模式标识

- **WHEN** 客户端指定 `search_mode=bm25` 并成功返回结果
- **THEN** 响应中 SHALL 包含表明本次为 BM25 检索的字段或元数据

### Requirement: 检索请求体元数据过滤

`POST /api/knowledge_base/collections/{collection_name}/search` 的请求体 SHALL 支持可选字段 `filters`（对象）。未提供时 SHALL 不过滤。提供时语义 SHALL 与 `kb-retrieval` 能力一致。知识库范围 SHALL 由路径参数 `collection_name` 指定，无需在 `filters` 中重复传递集合名。

#### Scenario: 指定集合且按 file_name 过滤

- **WHEN** 客户端请求 `POST .../collections/my_kb/search` 且 body 含 `query` 与 `filters.file_name`
- **THEN** 检索 SHALL 仅在 `my_kb` 集合内执行，且结果 SHALL 受 `file_name` 约束

#### Scenario: 旧客户端不传 filters

- **WHEN** 请求体不包含 `filters`
- **THEN** 系统 SHALL 在整个集合范围内检索，行为与添加该字段前一致

### Requirement: Markdown 标题分块入库写入 metadata

文档上传流水线 SHALL 一律按 Markdown 标题分块，将 `Header_1`~`Header_4`、`header_path` 等写入向量点 payload，而 SHALL NOT 将全部标题字段写为空字符串（解析失败时的内部滑窗回退除外）。

#### Scenario: 上传后分片详情含 header_path

- **WHEN** 客户端上传含多级标题的 Markdown 并查询分片详情
- **THEN** 至少一个分片的可查字段 SHALL 包含非空的 `header_path` 或相应 `Header_n`

## MODIFIED Requirements

### Requirement: 检索与上传

系统 SHALL 提供向量检索、BM25 检索、混合检索及文档上传解析流水线；检索 SHALL 委托 `kb-retrieval` 门面执行。上传仍将文件内容写入向量存储并与集合配置一致；大文件须受大小与超时约束。

#### Scenario: 上传成功

- **WHEN** 客户端上传允许格式且未超限制的文件到指定集合
- **THEN** 系统 SHALL 返回成功结构并在集合中可检索到新文档或分片

#### Scenario: 向量检索成功

- **WHEN** 客户端调用检索接口且 `search_mode` 为 `vector` 或未指定
- **THEN** 系统 SHALL 返回基于向量相似度的排序结果
