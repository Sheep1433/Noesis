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
