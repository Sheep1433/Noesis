# knowledge-base Specification

## Purpose

本能力是知识库 RAG **统一规格**：HTTP API 与集合配置、DeepDoc 文档解析、分块、hybrid 检索门面、以及单集合评测入口。关系数据在 PostgreSQL；向量在 Qdrant。详细设计可对照 `docs/prd/knowledge-base/`；在线 Agent 引用见 `agent-profiles`（COMMON_QA / TEST_CASE）。
## Requirements
### Requirement: 集合配置与 HTTP API

系统 SHALL 提供知识库集合的配置读写、文档上传/入库、检索等 HTTP API（前缀以现行 `/api/kb` 或等价为准）。集合级 `processing_params` / `query_params` SHALL 持久化在 PostgreSQL（表如 `kb_collection_config`），**SHALL NOT** 再以 MySQL 为权威。

#### Scenario: 读集合配置

- **WHEN** 客户端请求已存在集合的配置
- **THEN** 返回 200 且含处理/查询参数

### Requirement: DeepDoc 为解析引擎

文档解析 SHALL 经 DeepDoc / ParserFactory；支持至少 pdf/docx/pptx/markdown/excel 等已实现格式。解析失败 SHALL 返回可定位错误，**SHALL NOT** 静默写入空向量。

#### Scenario: Markdown 解析

- **WHEN** 上传 `.md` 并触发解析
- **THEN** 系统 SHALL 产出可用于分块的文本内容

### Requirement: 分块

系统 SHALL 经 DeepDocChunkAdapter（或现行适配器）按集合模板分块；块元数据 SHALL 足以支撑引用（文档 id、位置等，按实现）。

#### Scenario: 分块非空

- **WHEN** 解析得到非空文档
- **THEN** 分块结果 SHALL 含至少一个 chunk（除非策略显式丢弃）

### Requirement: hybrid 检索门面

`KbRetrievalService`（或现行门面）SHALL 提供 hybrid（向量 + 关键词等已实现组合）与可选 rerank；查询参数来自集合配置与请求覆盖。外部 Qdrant 404 等 SHALL 单独处理，勿一律 500。

#### Scenario: 检索返回命中

- **WHEN** 集合已有向量且查询相关
- **THEN** API/服务 SHALL 返回带分值的命中列表

### Requirement: 单集合评测入口

`evals.kb`（或现行 CLI）SHALL 能对单集合跑检索评测；与 `evals.case` 的 RAG phase 互补，职责边界在 `offline-evals` 索引。

#### Scenario: CLI 可跑

- **WHEN** 运维按文档执行 kb 评测命令
- **THEN** SHALL 产出可读的指标/报告文件或 stdout 摘要

### Requirement: 知识库命中 SHALL 提供稳定 evidence identity

知识库检索门面 SHALL 为每个最终命中提供 versioned evidence envelope，至少包含 `document_id`、`document_version_id`、`segment_id`、title、受限 excerpt 与可选 typed locator。KB 工具 SHALL NOT 向 Agent 输出 run-local `evidence_id`；该 ID 仅由平台消息构建器在登记检索结果时分配，用于检索结果块的稳定去重和恢复。三层持久身份 SHALL 跨 hybrid、BM25、vector 与 rerank 路径保留。数组位置、chunk index、内容 hash 和 Qdrant point id SHALL NOT 作为长期公开 citation identity。

#### Scenario: BM25 命中进入 rerank

- **WHEN** hybrid 检索的 BM25 命中经过 rerank 后进入最终结果
- **THEN** 最终 hit SHALL 保留相同 document、version、segment identity 与 locator

#### Scenario: 同一轮重复命中

- **WHEN** 多次或并行检索命中同一 document version segment
- **THEN** 平台 retrieval manifest SHALL 能确定性识别同一 evidence，并保留关联的 tool call
- **AND** 多次或并行工具调用 SHALL 复用同一个 run-local evidence id

### Requirement: 来源元数据 SHALL 支持 Prompt 引用与可点击回源

知识库检索结果 SHALL 向 Agent 提供可读的文件名、Collection、excerpt 与可用 locator，供模型生成编号引用和参考资料列表。客户端 SHALL 将唯一匹配的文件名与 Collection 转换为受认证保护的 Collection 文档路由；面向用户的 Prompt SHALL NOT 要求模型输出内部 identity。

#### Scenario: 检索结果包含页码

- **WHEN** 知识库命中包含 page locator
- **THEN** 工具结果 SHALL 保留页码和文件名
- **AND** 模型 MAY 在 `### 参考资料` 中展示该定位信息
