## ADDED Requirements

### Requirement: 知识库引擎与数据访问 SHALL 位于 harness 包

知识库 RAG 引擎——DeepDoc 文档解析、分块、向量嵌入、hybrid 检索门面、rerank——SHALL 位于 harness 包 `packages/harness/noesis/knowledge/`，作为 `noesis.knowledge` 子系统，SHALL NOT 位于平台域 `noesis_server/kb/`。知识库集合配置的 PostgreSQL 持久化（ORM model、repository）SHALL 位于 harness `noesis.storage` 与 `noesis.repositories`，SHALL NOT 位于平台 `noesis_server.services`、`noesis_server.models` 或 `noesis_server.infrastructure`。平台 SHALL 仅保留薄 HTTP API（`noesis_server/api/knowledge_base_api.py`），直调 `noesis.knowledge` 并在边缘做 HTTP 翻译。

#### Scenario: 平台不再持有引擎与配置持久化

- **WHEN** 扫描 `backend/noesis_server/`
- **THEN** SHALL NOT 存在 `kb/` Python 包、`services/knowledge_base_service.py`、`services/kb_collection_config_service.py`、`models/kb_models.py`、`infrastructure/database/`
- **AND** `noesis_server.kb` SHALL NOT 作为可 import 模块存在

#### Scenario: 平台 API 直调 harness

- **WHEN** `knowledge_base_api` 处理请求
- **THEN** SHALL import `noesis.knowledge.runtime` 单例并直调
- **AND** SHALL NOT 在平台域复制引擎或配置持久化逻辑

### Requirement: 集合配置 SHALL 由 harness repository 直读

知识库引擎读取集合级 `processing_params` / `query_params` 时，SHALL 经 `noesis.repositories` 的 repository 直接读取 PostgreSQL，SHALL NOT 经依赖注入端口、SHALL NOT import 平台 ORM 或 `kb_collection_config_service`。DB session SHALL 由 `noesis.storage.postgres.manager.pg_manager` 提供。Agent 工具线程内的同步读配置 SHALL 经 repository 的同步 session 路径，SHALL NOT 另建平台 sync engine。

#### Scenario: 引擎不依赖平台注入

- **WHEN** 审查 `noesis.knowledge/**` / `noesis.repositories/**` 源码
- **THEN** SHALL NOT 静态 import `noesis_server.models` / `noesis_server.services` / `noesis_server.infrastructure`
- **AND** SHALL NOT 调用 `require_kb_collection_config` 等注入函数

#### Scenario: 同步读配置不另建平台 engine

- **WHEN** Agent 工具在线程内同步读取集合配置
- **THEN** SHALL 经 `noesis.repositories` 同步 session（`pg_manager.get_sync_session`）取得
- **AND** SHALL NOT 存在平台 `kb_collection_config_service` 自建的独立 sync engine

## MODIFIED Requirements

### Requirement: DeepDoc 为解析引擎

文档解析 SHALL 经 `noesis.knowledge.parser` 的 DeepDoc / `ParserFactory`；支持至少 pdf/docx/pptx/markdown/excel 等已实现格式。解析失败 SHALL 返回可定位错误，**SHALL NOT** 静默写入空向量。DeepDoc 与 `_ragflow_compat` 为 vendored 代码（Apache-2.0），随 `noesis.knowledge` 平移，保留 `UPSTREAM.md` / `NOTICE`。

#### Scenario: Markdown 解析

- **WHEN** 上传 `.md` 并触发解析
- **THEN** `noesis.knowledge.parser` SHALL 产出可用于分块的文本内容

### Requirement: 分块

系统 SHALL 经 `noesis.knowledge.chunking` 的 chunk adapter（或现行适配器）按集合模板分块；块元数据 SHALL 足以支撑引用（文档 id、位置等，按实现）。

#### Scenario: 分块非空

- **WHEN** 解析得到非空文档
- **THEN** `noesis.knowledge.chunking` 分块结果 SHALL 含至少一个 chunk（除非策略显式丢弃）

### Requirement: hybrid 检索门面

`noesis.knowledge.retrieval.KbRetrievalService`（或现行门面）SHALL 提供 hybrid（向量 + 关键词等已实现组合）与可选 rerank；查询参数来自集合配置（经 `noesis.repositories`）与请求覆盖。外部 Qdrant 404 等 SHALL 单独处理，勿一律 500。harness 工具 SHALL 直接 import 该门面，SHALL NOT 经 `noesis.runtime.deps` 注入取得。

#### Scenario: 检索返回命中

- **WHEN** 集合已有向量且查询相关
- **THEN** API/服务经 `noesis.knowledge` 检索 SHALL 返回带分值的命中列表

#### Scenario: 工具直连门面

- **WHEN** `kb_search_tool` 调用检索
- **THEN** SHALL 直接 import `noesis.knowledge.KbRetrievalService`
- **AND** SHALL NOT 经 `require_kb_retrieval_service` 注入

### Requirement: 集合配置与 HTTP API

系统 SHALL 提供知识库集合的配置读写、文档上传/入库、检索等 HTTP API（前缀以现行 `/api/kb` 或等价为准）。集合级 `processing_params` / `query_params` SHALL 持久化在 PostgreSQL（表如 `kb_collection_config`），**SHALL NOT** 再以 MySQL 为权威。集合配置的 DB 持久化 SHALL 由 harness `noesis.repositories` 拥有，DB engine 由 `noesis.storage` 拥有；平台 API SHALL 直调 `noesis.knowledge` 单例并在边缘做 HTTP 翻译，SHALL NOT 保留夹层应用 service。

#### Scenario: 读集合配置

- **WHEN** 客户端请求已存在集合的配置
- **THEN** API 经 `noesis.knowledge` 单例读取，返回 200 且含处理/查询参数

#### Scenario: 写集合配置经 harness repository

- **WHEN** 客户端更新集合 `query_params`
- **THEN** `noesis.knowledge.manager` SHALL 经 `noesis.repositories` 持久化
- **AND** SHALL NOT 经平台 `kb_collection_config_service`
